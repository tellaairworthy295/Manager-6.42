from pwright.async_pm import AsyncPlaywrightManager

class PlaywrightContext:
    """
    Async context manager for Playwright browser context.
    """
    def __init__(self, manager: AsyncPlaywrightManager, **kwargs):
        self.manager = manager
        self.kwargs = kwargs
        self.context = None

    async def __aenter__(self):
        self.context = await self.manager.new_context(**self.kwargs)
        # Block window.open to suppress popups
        await self.context.add_init_script("window.open = () => null;")
        # Accept downloads, ignore HTTPS errors are already in kwargs
        return self.context

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self.context:
                await self.context.close()
        except Exception as e:
            pass