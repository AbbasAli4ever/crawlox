"""
WebSocket event protocol.

Client → Server:
  {"action": "subscribe", "channel": "task_{task_id}"}
  {"action": "unsubscribe", "channel": "task_{task_id}"}
  {"action": "captcha:solution", "task_id": "...", "solution": "..."}

Server → Client:
  {"event": "task:status_update", "data": {"task_id": "...", "status": "..."}}
  {"event": "captcha:required",   "data": {"task_id": "...", "captcha_type": "...", "sitekey": "...", "page_url": "...", "screenshot_b64": "..."}}
  {"event": "task:completed",     "data": {"task_id": "...", "total_items": N}}
  {"event": "task:failed",        "data": {"task_id": "...", "error": "..."}}
  {"event": "error",              "data": {"message": "..."}}
  {"event": "subscribed",         "data": {"channel": "..."}}
"""

# Server → Client event names
TASK_STATUS_UPDATE = "task:status_update"
CAPTCHA_REQUIRED = "captcha:required"
TASK_COMPLETED = "task:completed"
TASK_FAILED = "task:failed"
ERROR = "error"
SUBSCRIBED = "subscribed"

# Client → Server action names
ACTION_SUBSCRIBE = "subscribe"
ACTION_UNSUBSCRIBE = "unsubscribe"
ACTION_CAPTCHA_SOLUTION = "captcha:solution"
