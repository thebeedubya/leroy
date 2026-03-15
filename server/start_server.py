"""
Startup wrapper for Python 3.12+ compatibility.
asyncio.get_event_loop() raises RuntimeError in Python 3.12+ when no loop exists.
This wrapper pre-creates the loop before server.main() calls get_event_loop().
"""
import asyncio

# Pre-create event loop so server.main() can call asyncio.get_event_loop()
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

import server
server.main()
