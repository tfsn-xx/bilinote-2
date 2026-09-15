from typing import Optional, Union

from app.utils.logger import get_logger
from app.utils.openai_client import build_openai_client

logging= get_logger(__name__)
class OpenAICompatibleProvider:
    def __init__(self, api_key: str, base_url: str, model: Union[str, None]=None):
        # build_openai_client：注入全局代理 + 校验 api_key 非空
        self.client = build_openai_client(api_key, base_url, key_label="模型供应商的 API Key")
        self.model = model

    @property
    def get_client(self):
        return self.client

    @staticmethod
    def test_connection(api_key: str, base_url: str, model: str) -> bool:
        """发一条最小化 chat completion 验证 key / base_url / model 三方都通。

        为什么不用 client.models.list()：
          - 部分代理 / 自建供应商不实现 /v1/models（如某些 OpenAI 兼容网关）
          - 部分供应商 key 在没有 inference 权限时 /v1/models 仍返回 200
        最终用户跑的就是 chat.completions.create，所以直接测它最忠实。
        使用非流式最小回答，避免“空迭代器也算连接成功”，同时不发送
        temperature/max_tokens 等容易被新模型或兼容网关拒绝的可选参数。
        """
        try:
            client = build_openai_client(
                api_key, base_url, key_label="模型供应商的 API Key", timeout=15.0,
            )
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "只回复 OK"}],
                stream=False,
            )
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise ValueError("模型返回为空，连通性测试未获得可用文本")
            logging.info(f"连通性测试成功（model={model}）")
            return True
        except Exception as e:
            logging.warning(f"连通性测试失败（model={model}）：{e}")
            return False
