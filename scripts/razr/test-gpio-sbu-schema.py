#!/usr/bin/env python3
"""Real dt-schema positive/negative fixtures; pass processed-schema.json as argv[1]."""
import pathlib, subprocess, sys, tempfile

schema=pathlib.Path(sys.argv[1]).resolve()
assert schema.is_file()
dts=r'''/dts-v1/;
/ {
    compatible = "test,fixture";
    #address-cells = <1>; #size-cells = <1>;
    gpio: gpio-controller@0 {
        compatible = "test,gpio";
        reg = <0x0 0x100>;
        gpio-controller; #gpio-cells = <2>;
    };
    rail: regulator {
        compatible = "regulator-fixed";
        regulator-name = "fixture";
    };
    connector {
        compatible = "usb-c-connector";
        ports {
            #address-cells = <1>; #size-cells = <0>;
            port@2 {
                reg = <2>;
                connector_sbu: endpoint { remote-endpoint=<&sbu_in>; };
            };
        };
    };
    sbu-mux {
        compatible = "willsemi,was7227q", "gpio-sbu-mux";
        enable-gpios = <&gpio 2 1>;
        select-gpios = <&gpio 71 0>;
        vcc-supply = <&rail>;
        mode-switch; orientation-switch;
        port {
            sbu_in: endpoint { remote-endpoint=<&connector_sbu>; };
        };
    };
};
'''

def check(name,text,diagnostic=None):
    with tempfile.TemporaryDirectory() as tmp:
        f=pathlib.Path(tmp)/"test.dts"
        b=pathlib.Path(tmp)/"test.dtb"
        f.write_text(text)
        subprocess.run(["dtc","-q","-I","dts","-O","dtb","-o",str(b),str(f)],check=True)
        p=subprocess.run(["dt-validate","-m","-l","gpio-sbu-mux","-s",str(schema),str(b)],
                         capture_output=True,text=True)
        output=p.stdout+p.stderr
        if diagnostic:
            assert diagnostic in output,(name,p.returncode,output)
        else:
            assert p.returncode==0 and not output.strip(),(name,p.returncode,output)
        print("PASS",name,"valid" if diagnostic is None else "expected diagnostic")

check("WAS7227Q VCC",dts)
check("WAS7227Q missing VCC",dts.replace("        vcc-supply = <&rail>;\n",""),"vcc-supply")
check("legacy Pericom without VCC",
      dts.replace('"willsemi,was7227q"','"pericom,pi3usb102"')
         .replace("        vcc-supply = <&rail>;\n",""))
check("invalid switch identity",
      dts.replace('"willsemi,was7227q"','"willsemi,was7227x"'),"compatible")
print("GPIO_SBU_SCHEMA_FIXTURES_PASS 4")
