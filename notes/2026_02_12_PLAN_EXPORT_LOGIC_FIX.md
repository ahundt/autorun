# Plan Export Logic Fix - Feb 12, 2026

## Executive Summary
The recent "Restoration" (commit 744c862) introduced severe formatting corruption and logical inconsistencies in `plan_export.py`. The recovery function is currently a "Zombie" that may execute but fails to communicate its results correctly back to the daemon.

## 1. Formatting Restoration
**Problem**: Triple-newlines between every line of code in the `recover_unexported_plans` handler.
**Action**: Compact the code back to standard PEP 8 formatting.

## 2. Logical Flow Fix
**Problem**: The function returns `None` at the end even after successful exports.
**Action**: Ensure that if any plans are recovered, we return a valid `ctx.respond("allow", ...)` message so the daemon can notify the user.

## 3. Robust Context Handling
**Problem**: Dependence on `ctx.event` for logging but `ctx.session_id` for logic.
**Action**: Verify that the PID-based fallback in `core.py` is providing a stable session identity before `PlanExport` attempts to acquire a lock.

## Corrected Implementation (Target for plugins/clautorun/src/clautorun/plan_export.py)

```python
@app.on("SessionStart")
def recover_unexported_plans(ctx: EventContext) -> Optional[Dict]:
    """Recover plans from Option 1 (fresh context) on session start."""
    logger.info(f"SessionStart handler called (event: {ctx.event})")

    try:
        config = PlanExportConfig.load()
        if not config.enabled:
            return None

        exporter = PlanExport(ctx, config)
        recovered_count = 0
        last_msg = ""

        for plan in exporter.get_unexported():
            result = exporter.export(plan)
            if result["success"]:
                recovered_count += 1
                last_msg = result['message']

        if recovered_count > 0:
            msg = f"📋 Recovered {recovered_count} plan(s) (from fresh context): {last_msg}"
            return ctx.respond("allow", msg)

    except SessionTimeoutError as e:
        logger.warning(f"SessionStart plan recovery timeout: {e}")
        return None  # Lock timeout, skip silently
    except Exception as e:
        logger.error(f"recover_unexported_plans error: {e}", exc_info=True)

    return None
```
