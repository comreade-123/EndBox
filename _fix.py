from pathlib import Path
p = Path("_devdata/tmp/verify_userdata.py"); t = p.read_text(encoding="utf-8")
t = t.replace("ROOT = Path(__file__).resolve().parent", "ROOT = Path(__file__).resolve().parents[2]")
p.write_text(t, encoding="utf-8")
print("fixed")
