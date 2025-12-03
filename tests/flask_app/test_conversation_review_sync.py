from unittest.mock import AsyncMock, MagicMock

from flask_app.conversation_review import _analyze_conversation_history


def test_analyze_history_uses_provided_loop(mocker):
	# Mock the async function
	mock_async_func = mocker.patch('flask_app.conversation_review._analyze_conversation_history_async', new_callable=AsyncMock)
	mock_async_func.return_value = {'needs_action': False}

	# Mock run_coroutine_threadsafe
	mock_run_threadsafe = mocker.patch('asyncio.run_coroutine_threadsafe')
	mock_future = MagicMock()
	mock_future.result.return_value = {'needs_action': False}
	mock_run_threadsafe.return_value = mock_future

	# Create a mock loop
	mock_loop = MagicMock()
	mock_loop.is_running.return_value = True

	# Call with loop
	result = _analyze_conversation_history([], loop=mock_loop)

	assert result == {'needs_action': False}
	mock_run_threadsafe.assert_called_once()
	assert mock_run_threadsafe.call_args[0][1] == mock_loop


def test_analyze_history_fallback_if_loop_not_running(mocker):
	# Mock the async function
	mock_async_func = mocker.patch('flask_app.conversation_review._analyze_conversation_history_async', new_callable=AsyncMock)
	mock_async_func.return_value = {'needs_action': False}

	# Mock asyncio.run
	mock_run = mocker.patch('asyncio.run')
	mock_run.return_value = {'needs_action': False}

	mock_loop = MagicMock()
	mock_loop.is_running.return_value = False

	result = _analyze_conversation_history([], loop=mock_loop)

	mock_run.assert_called_once()


def test_analyze_history_default_uses_asyncio_run(mocker):
	# Mock the async function
	mock_async_func = mocker.patch('flask_app.conversation_review._analyze_conversation_history_async', new_callable=AsyncMock)
	mock_async_func.return_value = {'needs_action': False}

	# Mock asyncio.run
	mock_run = mocker.patch('asyncio.run')
	mock_run.return_value = {'needs_action': False}

	result = _analyze_conversation_history([])

	mock_run.assert_called_once()
