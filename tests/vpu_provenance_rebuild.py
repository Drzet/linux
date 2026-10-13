#!/usr/bin/env python3
"""Reconstruct a provenance-corrected Iris v5 series without rewriting tested history."""
import email
import email.policy
import email.utils
import gzip
import hashlib
import mailbox
import os
import pathlib
import re
import subprocess
import tempfile
import urllib.request

BASE="869cf4a6ad21afcc2435c4e2d1d4fa9ab33fb4bf"
ORIGINAL="ec9a8949cfbd1b05cf01553bffc39719ee1a16a7"
BRANCH="razr/vpu-provenance-v5"
STEM="20260926-vpu_iommu_iova_handling-v5"
SUFFIX="0322ca5dc10c@oss.qualcomm.com"
IMPORTS={"7d7038b2":1,"b8d974e5":4,"6619b5ec":5,"3e4c8aae":6,"48606cc3":7}

def git(*cmd, input=None, env=None):
    p=subprocess.run(["git",*cmd],input=input,text=True,env=env,
                     capture_output=True,check=True)
    return p.stdout.strip()

def source_mbox():
    mid=f"{STEM}-0-{SUFFIX}"
    errors=[]
    for inbox in ["all","linux-media","linux-arm-msm"]:
        url=f"https://lore.kernel.org/{inbox}/{mid}/t.mbox.gz"
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"b4/0.15.0",
                  "Accept":"application/gzip, application/mbox, */*"})
            with urllib.request.urlopen(req,timeout=35) as r:
                data=r.read(4000000)
            if len(data)==4000000: raise ValueError("mbox too large")
            raw=gzip.decompress(data) if data.startswith(b"\x1f\x8b") else data
            if b"Message-ID:" not in raw and b"Message-Id:" not in raw:
                raise ValueError("not email mbox")
            return url,data,raw
        except Exception as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError("Source retrieval failed:\n"+"\n".join(errors))

def parse(raw):
    with tempfile.TemporaryDirectory() as temp:
        p=pathlib.Path(temp)/"thread.mbox";p.write_bytes(raw)
        msgs={}
        for msg in mailbox.mbox(str(p),factory=lambda fd:
                email.message_from_binary_file(fd,policy=email.policy.default)):
            mid=str(msg.get("Message-ID","")).strip("<> \r\n")
            m=re.fullmatch(re.escape(STEM)+r"-(\d+)-"+re.escape(SUFFIX),mid)
            if m: msgs[int(m.group(1))]=msg
    missing={0,1,4,5,6,7}-set(msgs)
    if missing: raise RuntimeError(f"Missing source messages: {missing}")
    return msgs

def patch_id(diff):
    p=subprocess.run(["git","patch-id","--stable"],input=diff,
                     text=True,capture_output=True,check=True)
    return p.stdout.split()[0] if p.stdout.strip() else "none"

def body_of(msg):
    if msg.is_multipart():
        parts=[p for p in msg.walk() if p.get_content_type()=="text/plain"]
        if not parts: raise RuntimeError("No text/plain patch")
        return parts[0].get_content()
    return msg.get_content()

def main():
    if os.getenv("GITHUB_ACTIONS")!="true":
        raise RuntimeError("CI only")
    shas=git("rev-list","--reverse",f"{BASE}..{ORIGINAL}").splitlines()
    if len(shas)!=13 or any(not any(s.startswith(k) for s in shas) for k in IMPORTS):
        raise RuntimeError("Unexpected backport commit sequence")
    url,data,raw=source_mbox()
    msgs=parse(raw)
    print("ORIGINAL_MBOX_URL",url)
    print("SOURCE_MBOX_SHA256",hashlib.sha256(data).hexdigest())
    print("DECODED_MBOX_SHA256",hashlib.sha256(raw).hexdigest())
    lines=["original_patch,original_message_id,old_sha,new_sha,backport_patch_id,source_patch_id"]
    parent=BASE
    for old in shas:
        number=next((n for k,n in IMPORTS.items() if old.startswith(k)),None)
        env=os.environ.copy()
        env["GIT_COMMITTER_NAME"]="GitHub Actions provenance reconstruction"
        env["GIT_COMMITTER_EMAIL"]="41898282+github-actions[bot]@users.noreply.github.com"
        if number is None:
            message=git("show","-s","--format=%B",old)
            env["GIT_AUTHOR_NAME"]=git("show","-s","--format=%an",old)
            env["GIT_AUTHOR_EMAIL"]=git("show","-s","--format=%ae",old)
            env["GIT_AUTHOR_DATE"]=git("show","-s","--format=%aI",old)
            mid="local"
            old_pid=source_pid="n/a"
        else:
            msg=msgs[number]
            name,address=email.utils.parseaddr(str(msg["From"]))
            if name!="Vikash Garodia" or not address.endswith("qualcomm.com"):
                raise RuntimeError(f"Unexpected source author {name} <{address}>")
            source=body_of(msg)
            subject=re.sub(r"^\[PATCH v5 \d+/\d+\]\s*","",str(msg["Subject"]))
            body=source.split("\n---\n",1)[0].strip()
            source_diff="\ndiff --git ".join(source.split("\ndiff --git ")[1:])
            if source_diff: source_diff="diff --git "+source_diff
            old_pid=patch_id(git("show","--format=","--no-ext-diff",old))
            source_pid=patch_id(source_diff)
            if old_pid!=source_pid:
                # Do not transfer review approval onto a changed backport diff.
                body="\n".join(x for x in body.splitlines() if not x.startswith(
                    ("Reviewed-by:","Acked-by:","Tested-by:")))
            mid=f"{STEM}-{number}-{SUFFIX}"
            message=(subject+"\n\n"+body+"\n\n"+
                     f"Upstream-Message-ID: <{mid}>\n"+
                     "Backport-note: Original upstream patch applied to a different "+
                     "kernel baseline; patch-id "+("matches" if old_pid==source_pid else "differs")+".\n")
            env["GIT_AUTHOR_NAME"]=name
            env["GIT_AUTHOR_EMAIL"]=address
            env["GIT_AUTHOR_DATE"]=str(msg["Date"])
        tree=git("rev-parse",f"{old}^{{tree}}")
        new=git("commit-tree",tree,"-p",parent,input=message,env=env)
        print("MAPPING",number or "local",old,new,mid,old_pid,source_pid)
        lines.append(f"{number or 'local'},{mid},{old},{new},{old_pid},{source_pid}")
        parent=new
    original_tree=git("rev-parse",f"{ORIGINAL}^{{tree}}")
    rebuilt_tree=git("rev-parse",f"{parent}^{{tree}}")
    if rebuilt_tree!=original_tree: raise RuntimeError("Source tree mismatch")
    print("SOURCE_TREE_EQUAL",original_tree,rebuilt_tree)
    pathlib.Path("/tmp/vpu-provenance.csv").write_text("\n".join(lines)+"\n")
    existing=subprocess.run(["git","ls-remote","--exit-code","origin",
        f"refs/heads/{BRANCH}"],capture_output=True,text=True)
    if existing.returncode==0: raise RuntimeError("Target branch exists, refusing overwrite")
    if existing.returncode!=2: raise RuntimeError("Remote ref check failed: "+existing.stderr)
    git("push","origin",f"{parent}:refs/heads/{BRANCH}")
    print("PUBLISHED",BRANCH,parent)
if __name__=="__main__":
    main()
