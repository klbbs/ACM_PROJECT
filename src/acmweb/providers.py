"""Provider capabilities are explicit; no tools or agent execution are enabled."""
from urllib.parse import urlsplit

PRESETS = [
    {'id':'openai','name':'OpenAI','base_url':'https://api.openai.com/v1','protocol':'responses','docs':'https://developers.openai.com/api/docs/guides/structured-outputs'},
    {'id':'deepseek','name':'DeepSeek','base_url':'https://api.deepseek.com','protocol':'chat','docs':'https://api-docs.deepseek.com/'},
    {'id':'anthropic','name':'Anthropic / Claude','base_url':'https://api.anthropic.com/v1','protocol':'anthropic','docs':'https://platform.claude.com/docs/en/api/messages/create'},
    {'id':'gemini','name':'Google Gemini（OpenAI 兼容接口）','base_url':'https://generativelanguage.googleapis.com/v1beta/openai','protocol':'chat','docs':'https://ai.google.dev/gemini-api/docs/openai'},
    {'id':'qwen','name':'阿里云百炼 / Qwen（中国）','base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1','protocol':'chat','docs':'https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope'},
    {'id':'qwen_global','name':'阿里云百炼 / Qwen（新加坡）','base_url':'https://dashscope-intl.aliyuncs.com/compatible-mode/v1','protocol':'chat','docs':'https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope'},
    {'id':'moonshot','name':'Moonshot / Kimi（中国）','base_url':'https://api.moonshot.cn/v1','protocol':'chat','docs':'https://platform.moonshot.cn/docs'},
    {'id':'siliconflow','name':'硅基流动 SiliconFlow','base_url':'https://api.siliconflow.cn/v1','protocol':'chat','docs':'https://api-docs.siliconflow.cn/docs/userguide/quickstart'},
    {'id':'glm','name':'智谱 GLM（按量 API）','base_url':'https://open.bigmodel.cn/api/paas/v4','protocol':'chat','docs':'https://docs.bigmodel.cn/api-reference'},
    {'id':'custom','name':'自定义 / OpenAI 兼容网关','base_url':'https://api.openai.com/v1','protocol':'chat','docs':''},
]
DEFAULT_HOSTS = ','.join(dict.fromkeys(urlsplit(p['base_url']).hostname for p in PRESETS))
