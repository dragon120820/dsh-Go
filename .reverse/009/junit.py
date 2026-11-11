import glob
import sys
import xml.etree.ElementTree as ET

files = sorted(sys.argv[1:])
print("R009: junit files=%d" % len(files))
for f in files:
    root = ET.parse(f).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for s in suites:
        print("R009: junit suite name=%s tests=%s failures=%s errors=%s skipped=%s time=%s file=%s" % (
            s.get("name"), s.get("tests"), s.get("failures"), s.get("errors"), s.get("skipped"), s.get("time"), f.replace("\\", "/").split("/")[-1]))
        for c in s.iter("testcase"):
            state = "pass"
            first = ""
            for kind in ("failure", "error", "skipped"):
                n = c.find(kind)
                if n is not None:
                    state = kind
                    txt = (n.get("message") or n.text or "").strip().splitlines()
                    first = txt[0] if txt else ""
                    if kind != "skipped":
                        body = (n.text or "").strip().splitlines()[:12]
                        for ln in body:
                            print("R009: junit detail| %s" % ln)
                    break
            print("R009: junit case class=%s name=%s time=%s state=%s %s" % (c.get("classname"), c.get("name"), c.get("time"), state, first))
