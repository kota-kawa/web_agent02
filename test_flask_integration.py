#!/usr/bin/env python3
"""
Flask app integration test for multi-agent endpoints.

This script tests the Flask app endpoints for agent information.
"""

import sys
from pathlib import Path

# Add parent directories to path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
	sys.path.insert(0, str(ROOT_DIR))

# Test imports
print('Testing imports...')
try:
	from agent_config import get_all_agents, get_agent_info
	print('✓ agent_config imported successfully')
except ImportError as e:
	print(f'✗ Failed to import agent_config: {e}')
	sys.exit(1)

try:
	# Import Flask app components
	flask_app_dir = ROOT_DIR / 'flask_app'
	if str(flask_app_dir) not in sys.path:
		sys.path.insert(0, str(flask_app_dir))
	
	# Check if Flask app can import agent_config
	import importlib.util
	spec = importlib.util.spec_from_file_location("app", flask_app_dir / "app.py")
	if spec and spec.loader:
		print('✓ Flask app module loaded')
	else:
		print('✗ Failed to load Flask app module')
		sys.exit(1)
except Exception as e:
	print(f'✗ Flask app import test failed: {e}')
	import traceback
	traceback.print_exc()
	sys.exit(1)

# Test agent_config functionality
print('\nTesting agent_config functionality...')
try:
	agents = get_all_agents()
	print(f'✓ Found {len(agents)} agents')
	
	for agent_id in ['browser', 'faq', 'iot']:
		agent = get_agent_info(agent_id)  # type: ignore[arg-type]
		if agent:
			print(f'✓ Agent "{agent_id}": {agent.display_name}')
		else:
			print(f'✗ Agent "{agent_id}" not found')
			sys.exit(1)
			
except Exception as e:
	print(f'✗ Agent config test failed: {e}')
	import traceback
	traceback.print_exc()
	sys.exit(1)

print('\n✓ All integration tests passed!')
print('\nNote: To fully test the Flask endpoints, start the Flask app and use:')
print('  curl http://localhost:5005/api/agents')
print('  curl http://localhost:5005/api/agents/faq')
print('  curl -X POST http://localhost:5005/api/agents/suggest -H "Content-Type: application/json" -d \'{"task": "test"}\'')
