"""데모 절차: 회원정보 - 등록 업종 추가.

서버가 함께 띄우는 가짜 사이트(`/demo-site/`)를 Playwright로 조작한다.
실제 서비스에 붙일 때는 같은 모양으로 함수를 만들고, 선택자와 확인 조건만 실제 화면에 맞추면 된다.
(`playwright codegen <주소>`로 녹화한 코드를 단계별로 나눠 넣으면 빠르다.)
"""
from __future__ import annotations

import os

from playwright.async_api import expect

from ..engine import StepContext

SITE_TITLE = "데모 입찰서비스 - 회원정보"


async def open_site(ctx: StepContext) -> str:
    page = await ctx.page()
    await page.goto(f"{ctx.base_url}/demo-site/")
    await expect(page).to_have_title(SITE_TITLE)
    return f"'{SITE_TITLE}' 페이지 열림"


async def login(ctx: StepContext) -> str:
    page = await ctx.page()
    # 비밀번호는 코드에 쓰지 않고 환경변수로 받는다. 데모 사이트는 아무 값이나 받는다.
    await page.get_by_label("아이디").fill(os.environ.get("PROCEDURE_DEMO_USER", "demo"))
    await page.get_by_label("비밀번호").fill(os.environ.get("PROCEDURE_DEMO_PASSWORD", "demo-password"))
    await page.get_by_role("button", name="로그인").click()
    await expect(page.get_by_role("heading", name="회원정보")).to_be_visible()
    return "로그인 후 회원정보 화면 진입 확인"


async def read_current(ctx: StepContext) -> str:
    page = await ctx.page()
    items = await page.locator("#upjongList li").all_inner_texts()
    ctx.state["upjong_before"] = items
    if ctx.inputs["upjong"] in items:
        raise ValueError(f"'{ctx.inputs['upjong']}'은(는) 이미 등록된 업종입니다")
    return "현재 등록 업종: " + (", ".join(items) or "(없음)")


async def fill_upjong(ctx: StepContext) -> str:
    page = await ctx.page()
    field = page.get_by_label("업종 추가")
    await field.fill(ctx.inputs["upjong"])
    await expect(field).to_have_value(ctx.inputs["upjong"])
    return f"입력칸에 '{ctx.inputs['upjong']}' 입력 확인 (아직 저장 전)"


async def save(ctx: StepContext) -> str:
    page = await ctx.page()
    await page.get_by_role("button", name="저장").click()
    status = page.get_by_role("status")
    await expect(status).to_contain_text("저장되었습니다")
    return f"저장 메시지 확인: {await status.inner_text()}"


async def verify(ctx: StepContext) -> str:
    page = await ctx.page()
    items = await page.locator("#upjongList li").all_inner_texts()
    before = ctx.state.get("upjong_before", [])
    expected = before + [ctx.inputs["upjong"]]
    if items != expected:
        raise AssertionError(f"반영 결과가 예상과 다릅니다. 예상: {expected}, 실제: {items}")
    return f"반영 확인: {', '.join(before)} → {', '.join(items)}"


ACTIONS = {
    "member_demo.open_site": open_site,
    "member_demo.login": login,
    "member_demo.read_current": read_current,
    "member_demo.fill_upjong": fill_upjong,
    "member_demo.save": save,
    "member_demo.verify": verify,
}
