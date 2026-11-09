"""System diagnostic entry point: python -m grimmlink_adapter.diagnostics."""

import asyncio

from grimmlink_adapter.services.diagnostics import main

if __name__ == "__main__":
    asyncio.run(main())
