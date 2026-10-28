"""Explicit local browser render check, with synthetic text and no OJ/AI calls."""
import asyncio
import os
import struct
import pytest
from acmhermes.render import ScreenshotRenderer


@pytest.mark.skipif(os.getenv('ACM_TEST_HERMES_BROWSER')!='1',reason='opt-in optional Chromium renderer check')
def test_actual_offline_browser_renders_chinese_math_and_all_pages(tmp_path):
    async def run():
        renderer=ScreenshotRenderer()
        await renderer.preflight()
        pages,images=await renderer.render({'title':'中文公式分页测试',
            'body_html':'<p>当 $$$n \\neq 0$$$ 时求值。</p><img src="http://127.0.0.1/secret">'+
                '<p>合成题面分页内容。</p>'*90,
            'samples':[{'input':'1\n','output':'2\n'}]},tmp_path/'output')
        assert len(pages)>=2 and images==[]
        for name in pages:
            data=(tmp_path/'output'/name).read_bytes()
            assert data.startswith(b'\x89PNG\r\n\x1a\n')
            width,height=struct.unpack('>II',data[16:24])
            assert width==1020 and 0<height<=3072
    asyncio.run(run())
