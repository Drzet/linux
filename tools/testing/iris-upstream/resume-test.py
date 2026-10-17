#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute production RESUME wrappers and serialization with a mocked queue.

Uses the existing extract/compile/run host-harness approach. Packet definitions,
firmware descriptors, port mapping and serializers are production source. Only
OS instance storage, unused descriptor callbacks and queue I/O are mocked.
No firmware, scheduler, DMA or hardware execution is claimed.
"""
import hashlib
import json
import pathlib
import re
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[3]
out = pathlib.Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
iris = root / "drivers/media/platform/qcom/iris"
sources = {}
extracted = {}


def read(name):
    path = iris / name
    data = path.read_bytes()
    sources[str(path.relative_to(root))] = hashlib.sha256(data).hexdigest()
    return data.decode()


def declaration(code, prefix):
    match = re.search(prefix + r" \{.*?^\};", code, re.M | re.S)
    assert match, prefix
    return match.group()


def function(code, name):
    match = re.search(r"(?m)^(?:static )?(?:void|u32|int) " + name +
                      r"\([^;]*?\)\n\{", code, re.S)
    assert match, name
    end = match.end()
    depth = 1
    while depth:
        depth += (code[end] == "{") - (code[end] == "}")
        end += 1
    text = code[match.start():end]
    extracted[name] = hashlib.sha256(text.encode()).hexdigest()
    return text


platform = read("iris_platform_common.h")
common = read("iris_hfi_common.h")
command = read("iris_hfi_gen2_command.c")
packet = read("iris_hfi_gen2_packet.c")
read("iris_hfi_gen2_packet.h")
read("iris_hfi_gen2_defines.h")
core = read("iris_core.h")
buffers = read("iris_buffer.h")
profiles = []
profile_code = []
for name in ("iris_platform_vpu2.c", "iris_platform_vpu3x.c"):
    code = read(name)
    for match in re.finditer(r"(?ms)^static const struct iris_firmware_desc (\w+) = \{.*?^\};", code):
        if ".firmware_data = &iris_hfi_gen2_data," in match.group():
            profiles.append(match.group(1))
            profile_code.append(match.group())
    if name == "iris_platform_vpu2.c":
        sm8475 = declaration(code, r"const struct iris_platform_data sm8475_data =")
        assert ".firmware_desc_gen2 = &iris_sm8475_gen2_desc," in sm8475
assert "iris_sm8475_gen2_desc" in profiles

prefix = r'''
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <linux/videodev2.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#define BIT(n) (1U << (n))
struct iris_inst;
struct iris_core;
#include "iris_hfi_gen2_packet.h"
'''
definitions = "\n".join([
    declaration(core, "enum domain_type"),
    declaration(buffers, "enum iris_buffer_type"),
    declaration(common, "enum hfi_packet_port_type"),
    declaration(common, "enum hfi_packet_payload_info"),
    declaration(common, "enum hfi_packet_host_flags"),
    declaration(platform, "struct iris_firmware_desc"),
])
setup = r'''
struct iris_firmware_data { int unused; };
static const struct iris_firmware_data iris_hfi_gen2_data;
static u32 iris_vpu_buf_size(struct iris_inst *inst, enum iris_buffer_type type)
{ (void)inst; (void)type; return 0; }
static u32 iris_vpu33_buf_size(struct iris_inst *inst, enum iris_buffer_type type)
{ (void)inst; (void)type; return 0; }
struct iris_core {
 const struct iris_firmware_desc *iris_firmware_desc;
 u32 header_id, packet_id;
};
struct iris_inst { struct iris_core *core; u32 session_id; enum domain_type domain; };
struct iris_inst_hfi_gen2 { struct iris_inst inst; struct iris_hfi_header *packet; };
#define to_iris_inst_hfi_gen2(p) \
 ((struct iris_inst_hfi_gen2 *)((char *)(p) - offsetof(struct iris_inst_hfi_gen2, inst)))
static unsigned assertions, cases;
#define CHECK(test) do { assertions++; if (!(test)) { \
 fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #test); exit(1); } } while (0)
static int queue_result;
static unsigned queue_calls, serialize_calls;
static u32 written_size, argument_type, argument_size;
static bool argument_null;
static u8 written[128];
static struct iris_core *written_core;
static void *expected_packet;
static int iris_hfi_queue_cmd_write(struct iris_core *core, void *packet, u32 size)
{
 CHECK(size <= sizeof(written));
 CHECK(packet == expected_packet);
 written_core = core;
 written_size = size;
 memcpy(written, packet, size);
 queue_calls++;
 return queue_result;
}
'''
serializer = "\n".join(function(packet, name) for name in (
    "iris_hfi_gen2_create_header", "iris_hfi_gen2_create_packet",
    "iris_hfi_gen2_packet_session_command"))
# Observe NONE/NULL/0 at the API boundary, then execute the real serializer.
spy = r'''
static void observe_session_command(struct iris_inst *inst, u32 type, u32 flags,
 u32 port, u32 session, u32 payload_type, void *payload, u32 size)
{
 serialize_calls++;
 argument_type = payload_type;
 argument_null = payload == NULL;
 argument_size = size;
 if (payload_type == HFI_PAYLOAD_NONE)
  CHECK(payload == NULL && size == 0);
 else
  CHECK(payload_type == HFI_PAYLOAD_U32 && payload != NULL && size == sizeof(u32));
 iris_hfi_gen2_packet_session_command(inst, type, flags, port, session,
                                    payload_type, payload, size);
}
#define iris_hfi_gen2_packet_session_command observe_session_command
'''
commands = "\n".join(function(command, name) for name in (
    "iris_hfi_gen2_get_port", "iris_hfi_gen2_session_resume",
    "iris_hfi_gen2_session_resume_drc", "iris_hfi_gen2_session_resume_drain"))
profile_table = "\n".join(' { &%s, %s },' % (name, "true" if name == "iris_sm8475_gen2_desc" else "false")
                          for name in profiles)
tests = r'''
#undef iris_hfi_gen2_packet_session_command
struct profile { const struct iris_firmware_desc *desc; bool no_payload; };
static const struct profile profiles[] = {
PROFILE_TABLE
};
static void run_case(const struct iris_firmware_desc *desc, bool no_payload,
                     bool drc, bool capture, bool decoder, int error)
{
 struct iris_core core = { .iris_firmware_desc = desc,
                          .header_id = UINT32_MAX, .packet_id = 0x12345678 };
 union { u64 align; u8 bytes[128]; } storage;
 struct iris_inst_hfi_gen2 instance = {
  .inst = { .core = &core, .session_id = 0x76543210,
            .domain = decoder ? DECODER : ENCODER },
  .packet = (void *)storage.bytes,
 };
 struct iris_firmware_desc saved = *desc;
 u32 plane = capture ? V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE :
                       V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
 u32 port = decoder == capture ? 2 : 1;
 u32 size = no_payload ? 64 : 68;
 u32 reason = drc ? 0x0100000c : 0x01000007;

 cases++;
 CHECK(desc->resume_without_payload == no_payload);
 memset(storage.bytes, 0xa5, sizeof(storage.bytes));
 expected_packet = instance.packet;
 queue_result = error;
 for (unsigned step = 0; step < 2; step++) {
  u32 header_id = core.header_id, packet_id = core.packet_id;
  u32 expected[17] = {
   size, 0x76543210, header_id, 0, 0, 0, 0, 1,
   size - 32, 0x01000008, 3, no_payload ? 0 : 1, port, packet_id, 0, 0, reason
  };
  int ret;

  memset(written, 0xcc, sizeof(written));
  queue_calls = serialize_calls = 0;
  ret = drc ? iris_hfi_gen2_session_resume_drc(&instance.inst, plane) :
              iris_hfi_gen2_session_resume_drain(&instance.inst, plane);
  CHECK(ret == error);
  CHECK(queue_calls == 1 && serialize_calls == 1);
  CHECK(written_core == &core);
  CHECK(written_size == size);
  CHECK(argument_type == (no_payload ? 0U : 1U));
  CHECK(argument_null == no_payload);
  CHECK(argument_size == (no_payload ? 0U : 4U));
  CHECK(memcmp(written, expected, size) == 0);
  CHECK(core.header_id == (u32)(header_id + 1));
  CHECK(core.packet_id == (u32)(packet_id + 1));
  CHECK(desc->firmware_data == saved.firmware_data);
  CHECK(desc->get_vpu_buffer_size == saved.get_vpu_buffer_size);
  CHECK(desc->fwname == saved.fwname);
  CHECK(desc->resume_without_payload == saved.resume_without_payload);
  CHECK(instance.inst.session_id == 0x76543210);
  CHECK(instance.inst.domain == (decoder ? DECODER : ENCODER));
  for (unsigned i = size; i < sizeof(written); i++)
   CHECK(written[i] == 0xcc); /* queue received no extra/stale bytes */
  for (unsigned i = size; i < sizeof(storage.bytes); i++)
   CHECK(storage.bytes[i] == 0xa5); /* builder did not extend its payload */
 }
}
static void matrix(const struct iris_firmware_desc *desc, bool no_payload)
{
 const int errors[] = { 0, -EIO, -ENOSPC };
 for (unsigned drc = 0; drc < 2; drc++)
  for (unsigned capture = 0; capture < 2; capture++)
   for (unsigned decoder = 0; decoder < 2; decoder++)
    for (unsigned e = 0; e < sizeof(errors) / sizeof(errors[0]); e++)
     run_case(desc, no_payload, drc, capture, decoder, errors[e]);
}
int main(void)
{
 struct iris_firmware_desc toggle = iris_sm8475_gen2_desc;
 CHECK(sizeof(struct iris_hfi_header) == 32);
 CHECK(sizeof(struct iris_hfi_packet) == 32);
 /* Execute every actual Gen2 descriptor, including all unchanged defaults. */
 for (unsigned p = 0; p < sizeof(profiles) / sizeof(profiles[0]); p++)
  matrix(profiles[p].desc, profiles[p].no_payload);
 /* Changing only this option must select either encoding on the same profile. */
 toggle.resume_without_payload = false;
 matrix(&toggle, false);
 toggle.resume_without_payload = true;
 matrix(&toggle, true);
 printf("PASS Gen2 RESUME: %u cases / %u assertions; %zu production profiles; "
        "DRC/drain, decoder/encoder ports, option states, queue errors, ID wrap\n",
        cases, assertions, sizeof(profiles) / sizeof(profiles[0]));
 return 0;
}
'''.replace("PROFILE_TABLE", profile_table)
code = "\n".join([prefix, definitions, setup, *profile_code, serializer, spy, commands, tests])
src = out / "resume-harness.c"
exe = out / "resume-harness"
src.write_text(code)
cmd = ["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
       "-fsanitize=undefined", "-g", "-I", str(iris), str(src), "-o", str(exe)]
subprocess.run(cmd, check=True)
proc = subprocess.run([str(exe)], text=True, capture_output=True)
(out / "resume.log").write_text(proc.stdout + proc.stderr)
assert proc.returncode == 0, proc.stdout + proc.stderr
result = {
    "kernel_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
    "sources_match_commit": subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *sources], cwd=root).returncode == 0,
    "source_sha256": sources,
    "production_function_sha256": extracted,
    "profiles": profiles,
    "harness_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
    "generated_source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
    "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
    "compiler": subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0],
    "compile_command": cmd,
    "result": proc.stdout.strip(),
    "hardware_tested": False,
}
(out / "resume-results.json").write_text(json.dumps(result, indent=2) + "\n")
print(proc.stdout.strip())
