from collections.abc import Callable
from typing import Any, TypeVar


F = TypeVar("F", bound=Callable[..., Any])


# @author 배세은
# @date 2026-05-06
# @relatedFR N/A
# @relatedUI TODO-UI-ID
# @description LLM/워크플로 관측(LangSmith) 트레이싱 보조 유틸. 현재는 하위호환용
# no-op 데코레이터로, 실제 트레이싱은 LangGraph 네이티브 + wrap_openai가 담당한다.
# 사용자 기능이 아니라 평가 워크플로 관측 인프라이므로 직접 매핑되는 FR/UI는 없다.
def trace(name: str | None = None, run_type: str = "chain") -> Callable[[F], F]:
    """No-op decorator kept for backwards compatibility.

    Workflow tracing is handled natively by LangGraph, which builds a single
    nested run tree (correct even across the worker threads used for parallel
    nodes) when ``LANGSMITH_TRACING`` is enabled. LLM cost is captured by the
    ``wrap_openai`` client in ``services.llm.client_service``.

    The previous implementation wrapped every node/agent in ``langsmith.traceable``,
    which tracks the parent run via a contextvar. Because LangGraph executes
    nodes in worker threads and contextvars do not cross thread boundaries,
    each node detached into its own root trace. Keeping this as a no-op removes
    those competing roots so the whole run shows up as one trace.
    """

    def decorator(func: F) -> F:
        return func

    return decorator
