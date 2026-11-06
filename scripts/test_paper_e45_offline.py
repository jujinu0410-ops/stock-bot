"""Load the production overlay as services; all HTTP is blocked, state temporary."""
import importlib.util
from pathlib import Path
import os,sys,types,tempfile,unittest,logging
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
runtime=root/'scratch/e45_image_runtime'
if not (runtime/'src/analysis/held_obv15_warning.py').exists():
    raise SystemExit('Missing read-only production image source runtime; see docs/E45_IMPLEMENTATION.md')
sys.path.insert(0,str(runtime))
os.environ['STOCKBOT_CLOUD_MODE']='1'
for key in ['KIWOOM_APP_KEY','KIWOOM_APP_SECRET','GMAIL_APP_PASSWORD','JEV_API_KEY']:
    os.environ[key]='OFFLINE_TEST_ONLY'
with tempfile.TemporaryDirectory(prefix='e45-test-') as state:
    os.environ['STOCKBOT_STATE_DIR']=state
    package=types.ModuleType('services');package.__path__=[str(root/'scripts/overlays/kiwoom_cache_20261016')]
    sys.modules['services']=package
    files=['tests/test_paper_e45_server.py','scripts/overlays/kiwoom_cache_20261016/test_cache_opt.py',
           'scripts/overlays/kiwoom_cache_20261016/test_market_inverse_early.py','tests/test_sparse_completed_45m.py']
    suite=unittest.TestSuite()
    # requests, including yfinance's fallback, cannot reach an actual market API.
    try:
        with patch('requests.sessions.Session.request',side_effect=AssertionError('OUTBOUND_HTTP_FORBIDDEN')),patch('socket.socket.connect',side_effect=AssertionError('OUTBOUND_NETWORK_FORBIDDEN')):
            for i,file in enumerate(files):
                spec=importlib.util.spec_from_file_location('e45_tests_'+str(i),root/file)
                module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
                suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
            result=unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        logging.shutdown()
    sys.exit(0 if result.wasSuccessful() else 1)
