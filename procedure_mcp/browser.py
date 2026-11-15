"""실행(run)별 브라우저 관리 (Playwright).

실행 하나에 브라우저 컨텍스트(시크릿 창) 하나를 붙여서, 승인을 기다리는 동안에도 로그인 상태와 화면이 유지되게 한다.
실행이 끝나면(완료·실패·거절·취소) 컨텍스트를 닫는다.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class BrowserManager:
    def __init__(self, *, headless: bool = True, executable_path: str | None = None, viewport: tuple[int, int] = (800, 720)):
        self.headless = headless
        self.executable_path = executable_path
        self.viewport = {"width": viewport[0], "height": viewport[1]}
        self._playwright: Any = None
        self._browser: Any = None
        self._contexts: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}
        self._start_lock: asyncio.Lock | None = None

    async def _ensure_browser(self) -> Any:
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._browser is None:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless, executable_path=self.executable_path
                )
        return self._browser

    async def page_for(self, run_id: str) -> Any:
        page = self._pages.get(run_id)
        if page is not None and not page.is_closed():
            return page
        browser = await self._ensure_browser()
        context = await browser.new_context(viewport=self.viewport, locale="ko-KR")
        page = await context.new_page()
        self._contexts[run_id] = context
        self._pages[run_id] = page
        return page

    async def screenshot(self, run_id: str, path: Path) -> bool:
        page = self._pages.get(run_id)
        if page is None or page.is_closed():
            return False
        await page.screenshot(path=str(path))
        return True

    async def release(self, run_id: str) -> None:
        self._pages.pop(run_id, None)
        context = self._contexts.pop(run_id, None)
        if context is not None:
            await context.close()

    async def close(self) -> None:
        for run_id in list(self._contexts):
            await self.release(run_id)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
