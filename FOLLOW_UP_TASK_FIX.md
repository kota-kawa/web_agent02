# Follow-up Task Fix: BrowserStateRequestEvent Handler Issue

## Problem Statement

After the first task completed successfully, when attempting to execute a second (follow-up) task, the system failed with the error:

```
Expected at least one handler to return a non-None result, but none did! ?▶ BrowserStateRequestEvent#482b ✅ -> {}
```

This error occurred repeatedly (4 times) causing the agent to stop execution.

## Root Cause Analysis

The issue was in the Flask application's `_run_agent()` method in `flask_app/app.py`. The problem occurred in this sequence:

1. **Initial Setup**: `attach_all_watchdogs()` was called to register all event handlers, including the critical `DOMWatchdog.on_BrowserStateRequestEvent` handler
2. **Follow-up Task Setup**: `agent.add_new_task(task)` was called for existing agents
3. **Event Bus Reset**: Inside `add_new_task()`, the method `_reset_eventbus()` was called which:
   - Created a new EventBus instance
   - Called `_refresh_browser_session_eventbus()` 
   - Set `session._watchdogs_attached = False` and cleared all watchdog references
   - **Did not reattach the watchdogs**
4. **Handler Loss**: The DOM watchdog's `on_BrowserStateRequestEvent` handler was no longer registered
5. **Failure**: When the agent started executing and tried to get browser state via `BrowserStateRequestEvent`, no handler was available to respond

## Solution

**Moved the `attach_all_watchdogs()` call to happen AFTER the agent setup is complete.**

### Before (Broken):
```python
async def _run_agent(self, task: str) -> AgentRunResult:
    session = await self._ensure_browser_session()
    
    # Attach watchdogs FIRST
    attach_watchdogs = getattr(session, 'attach_all_watchdogs', None)
    if attach_watchdogs is not None:
        await attach_watchdogs()  # Handlers registered
    
    # ... setup agent ...
    
    if existing_agent is None:
        agent = _create_new_agent(task)
    else:
        agent.add_new_task(task)  # <-- This resets EventBus and clears handlers!
        
    # Agent runs with no handlers available
    history = await agent.run(max_steps=self._max_steps)
```

### After (Fixed):
```python
async def _run_agent(self, task: str) -> AgentRunResult:
    session = await self._ensure_browser_session()
    
    # ... setup agent first ...
    
    if existing_agent is None:
        agent = _create_new_agent(task)
    else:
        agent.add_new_task(task)  # EventBus reset happens here
    
    # Attach watchdogs AFTER agent setup is complete
    attach_watchdogs = getattr(session, 'attach_all_watchdogs', None)
    if attach_watchdogs is not None:
        await attach_watchdogs()  # Handlers re-registered after reset
        
    # Agent runs with handlers properly available
    history = await agent.run(max_steps=self._max_steps)
```

## Technical Details

The fix ensures that:

1. **Event Bus Reset**: `agent.add_new_task()` can safely reset the EventBus as designed
2. **Watchdog Reattachment**: Watchdogs are reattached after the reset, ensuring all handlers are available
3. **Handler Availability**: When the agent starts execution, `BrowserStateRequestEvent` has a registered handler (`DOMWatchdog.on_BrowserStateRequestEvent`)
4. **Backward Compatibility**: New agents (first task) still work the same way since watchdogs are attached before execution

## Impact

- ✅ **Fixes the primary issue**: Second tasks no longer fail with "Expected at least one handler" errors
- ✅ **Minimal code change**: Only reordered existing logic, no new functionality added  
- ✅ **Preserves existing behavior**: First tasks and new agents continue to work as before
- ✅ **No performance impact**: Same operations, just in the correct order

## Files Changed

- `flask_app/app.py`: Moved `attach_all_watchdogs()` call to after agent setup in `_run_agent()` method

This fix resolves the follow-up task execution issue by ensuring event handlers are properly available when needed.