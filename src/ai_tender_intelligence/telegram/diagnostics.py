"""Payload-free timings: fixed operation/stage codes and numbers only."""

import json
from time import perf_counter


class Diagnostics:
    OPERATIONS = {"getWebhookInfo", "getUpdates", "sendMessage", "editMessageText",
                  "editMessageReplyMarkup", "answerCallbackQuery", "interaction", "batch", "publication"}
    PHASES = {"http", "ack", "handle", "render", "poll_cycle", "clock", "recovery", "fetched_wait", "ui_batch"}
    STAGES = {"open", "read", "decode", "api", "complete", "unknown", "retry", "exhausted", "not_modified"}
    OUTCOMES = {"ok", "failed", "uncertain", "skipped", "recovered"}

    def __init__(self, sink=None, timer=perf_counter):
        self.sink, self.timer = sink, timer

    def record(self, phase, operation, elapsed=0, *, stage="complete", outcome="ok", skew_ms=None, failure_kind=None):
        if phase not in self.PHASES or operation not in self.OPERATIONS or stage not in self.STAGES or outcome not in self.OUTCOMES:
            raise ValueError("Unknown diagnostic code")
        data = {"phase": phase, "operation": operation, "stage": stage,
                "outcome": outcome, "elapsed_ms": round(max(0, elapsed) * 1000, 3)}
        if skew_ms is not None:
            data["clock_skew_ms"] = round(skew_ms, 3)
        if failure_kind is not None:
            if failure_kind not in {"timeout", "tls", "decode", "connection", "api", "other"}:
                raise ValueError("Unknown diagnostic failure category")
            data["failure_kind"] = failure_kind
        if self.sink:
            self.sink(data)


def console_timing(data):
    print(json.dumps(data, ensure_ascii=True))
