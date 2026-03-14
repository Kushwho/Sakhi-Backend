import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.routes:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        loop="asyncio",  # ← forces uvicorn to use SelectorEventLoop
    )