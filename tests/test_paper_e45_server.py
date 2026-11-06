"""E45 archival regression is isolated from the active M30 Python namespace."""
import json,os,pathlib,subprocess,sys,unittest

class ArchivedE45ServerRegression(unittest.TestCase):
    def test_all_original_e45_server_cases(self):
        try:
            import flask
        except ImportError:
            raise unittest.SkipTest("Flask not installed in local environment (archive45 server test requires flask)")
        for candidate in [pathlib.Path(__file__).resolve().parents[1], pathlib.Path(__file__).resolve().parents[2]]:
            if (candidate / "archive45").exists():
                base = candidate
                break
        else:
            base = pathlib.Path(__file__).resolve().parents[2]
        env={**os.environ,"STOCKBOT_TEST_MODE":"1","STOCKBOT_CLOUD_MODE":"1","KIWOOM_USE_MOCK":"True","PYTHONUTF8":"1",
             "PYTHONPATH":str(base/"runtime/tests/test_runtime_shims") if (base/"runtime").exists() else str(base/"tests/test_runtime_shims")}
        archive_dir = base / "archive45"
        evidence_dir = base / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        result=subprocess.run([sys.executable,"-m","unittest","discover","-s","tests","-p","test_paper_e45_server.py","-v"],cwd=archive_dir,env=env,capture_output=True,text=True,encoding="utf-8",timeout=120)
        (evidence_dir/"archive45-server.log").write_text(result.stdout+result.stderr,encoding="utf-8")
        self.assertEqual(result.returncode,0,(result.stdout+result.stderr)[-6000:])
        self.assertNotIn("skipped=",result.stderr)
