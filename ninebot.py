#!/usr/bin/env python3
# cron "0 7 * * *"
"""九号 App 青龙合并任务：签到、分享领奖和盲盒领取。"""

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


SIGN_URL = "https://cn-cbu-gateway.ninebot.com/portal/api/user-sign/v2/sign"
SHARE_URL = "https://cn-cbu-gateway.ninebot.com/app-api/circle/v1/share-callback"
REWARD_URL = "https://cn-cbu-gateway.ninebot.com/portal/self-service/task/reward"
BLIND_BOX_LIST_URL = "https://cn-cbu-gateway.ninebot.com/portal/api/user-sign/v2/blind-box/list"
BLIND_BOX_RECEIVE_URL = "https://cn-cbu-gateway.ninebot.com/portal/api/user-sign/v2/blind-box/receive"
SHARE_REWARD_TASK_ID = "1823622692036079618"
REWARD_TYPE_LABELS = {1: "经验", 2: "N币"}


class ConfigurationError(ValueError):
    """表示青龙环境变量缺失。"""


@dataclass(frozen=True)
class TaskConfig:
    token: str
    device_id: str
    share_payload: Optional[str]

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "TaskConfig":
        missing = [name for name in ("NINEBOT_TOKEN", "NINEBOT_DEVICE_ID") if not environment.get(name)]
        if missing:
            raise ConfigurationError(f"缺少必填环境变量：{', '.join(missing)}")
        return cls(
            token=environment["NINEBOT_TOKEN"],
            device_id=environment["NINEBOT_DEVICE_ID"],
            share_payload=environment.get("NINEBOT_SHARE_PAYLOAD") or None,
        )


@dataclass
class SummaryItem:
    name: str
    status: str
    detail: str


@dataclass
class TaskSummary:
    items: list[SummaryItem] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str) -> None:
        self.items.append(SummaryItem(name, status, detail))

    def status_for(self, name: str) -> str:
        for item in self.items:
            if item.name == name:
                return item.status
        raise KeyError(f"未找到任务步骤：{name}")

    def render(self) -> str:
        lines = ["# 九号任务汇总"]
        for item in self.items:
            lines.append(f"- {item.name}：{item.status} - {item.detail}")
        return "\n".join(lines)


class NinebotRunner:
    def __init__(self, config: TaskConfig, session: Any):
        self.config = config
        self.session = session
        self.summary = TaskSummary()

    def run(self) -> TaskSummary:
        self._daily_sign()
        self._share_and_collect_reward()
        self._check_blind_boxes()
        return self.summary

    def _request(self, method: str, url: str, headers: dict, **kwargs) -> tuple[int, dict]:
        response = self.session.request(method, url, headers=headers, timeout=(10, 30), **kwargs)
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError(f"接口返回不是 JSON：{error}") from error
        if not isinstance(payload, dict):
            raise RuntimeError("接口返回 JSON 不是对象")
        return response.status_code, payload

    def _portal_headers(self) -> dict:
        return {
            "Authorization": self.config.token,
            "Content-Type": "application/json",
            "platform": "h5",
            "language": "zh",
            "sys_language": "zh-CN",
            "device_id": self.config.device_id,
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
        }

    @staticmethod
    def _failure_detail(status_code: int, payload: dict) -> str:
        if status_code != 200:
            return f"HTTP 状态码 {status_code}"
        return str(payload.get("msg") or payload.get("message") or "接口返回失败")

    def _daily_sign(self) -> None:
        try:
            status_code, payload = self._request(
                "POST",
                SIGN_URL,
                self._portal_headers(),
                json={"deviceId": self.config.device_id},
            )
            if status_code == 200 and payload.get("code") == 0:
                self.summary.add("每日签到", "成功", str(payload.get("msg") or "签到成功"))
            else:
                self.summary.add("每日签到", "失败", self._failure_detail(status_code, payload))
        except Exception as error:
            self.summary.add("每日签到", "失败", f"请求异常：{error}")

    def _share_and_collect_reward(self) -> None:
        if not self.config.share_payload:
            self.summary.add("分享领奖", "跳过", "未设置 NINEBOT_SHARE_PAYLOAD")
            return

        try:
            share_payload = json.loads(self.config.share_payload)
            if not isinstance(share_payload, dict):
                raise ValueError("分享载荷必须是 JSON 对象")
            share_headers = {
                "Access-Token": self.config.token,
                "Device-Id": self.config.device_id,
                "Content-Type": "application/json",
                "Platform": "iOS",
                "Language": "zh",
                "Accept": "application/json",
            }
            status_code, payload = self._request("POST", SHARE_URL, share_headers, json=share_payload)
            if status_code != 200 or payload.get("code") != 0:
                self.summary.add("分享领奖", "失败", f"分享失败：{self._failure_detail(status_code, payload)}")
                return

            status_code, payload = self._request(
                "POST",
                REWARD_URL,
                self._portal_headers(),
                json={"taskId": SHARE_REWARD_TASK_ID},
            )
            if status_code == 200 and payload.get("code") == 0:
                self.summary.add("分享领奖", "成功", str(payload.get("msg") or "分享任务奖励领取成功"))
            else:
                self.summary.add("分享领奖", "失败", f"领奖失败：{self._failure_detail(status_code, payload)}")
        except Exception as error:
            self.summary.add("分享领奖", "失败", f"请求异常：{error}")

    @staticmethod
    def _is_receivable(box: dict) -> bool:
        return box.get("rewardStatus") == 1 or box.get("leftDaysToOpen") in (None, 0, "0")

    @staticmethod
    def _reward_description(payload: dict) -> str:
        data = payload.get("data") or {}
        reward_value = data.get("rewardValue")
        reward_label = REWARD_TYPE_LABELS.get(data.get("rewardType"))
        if reward_label and reward_value is not None:
            return f"{reward_label} {reward_value}"
        if reward_value is not None:
            return str(reward_value)
        return "奖励已到账"

    def _receive_blind_box(self, award_days, reward_id) -> tuple[bool, str]:
        if not reward_id:
            return False, f"{award_days}天盲盒缺少 rewardId"
        try:
            status_code, payload = self._request(
                "POST",
                BLIND_BOX_RECEIVE_URL,
                self._portal_headers(),
                json={"rewardId": str(reward_id)},
            )
            if status_code == 200 and payload.get("code") == 0:
                return True, f"{award_days}天盲盒领取成功: {self._reward_description(payload)}"
            return False, f"{award_days}天盲盒领取失败: {self._failure_detail(status_code, payload)}"
        except Exception as error:
            return False, f"{award_days}天盲盒请求异常：{error}"

    def _check_blind_boxes(self) -> None:
        try:
            status_code, payload = self._request(
                "GET",
                BLIND_BOX_LIST_URL,
                self._portal_headers(),
                params={"t": int(time.time() * 1000)},
            )
            if status_code != 200 or payload.get("code") != 0:
                self.summary.add("盲盒检查", "失败", self._failure_detail(status_code, payload))
                return

            boxes = (payload.get("data") or {}).get("notOpenedBoxes") or []
            details = [f"发现 {len(boxes)} 个未开启盲盒"]
            failures = []
            for box in boxes:
                if not self._is_receivable(box):
                    continue
                reward_ids = box.get("blindBoxIds") or []
                success, detail = self._receive_blind_box(box.get("awardDays", "未知"), reward_ids[0] if reward_ids else None)
                details.append(detail)
                if not success:
                    failures.append(detail)
            status = "失败" if failures else "成功"
            self.summary.add("盲盒检查", status, "；".join(details))
        except Exception as error:
            self.summary.add("盲盒检查", "失败", f"请求异常：{error}")


def send_notification(content: str) -> None:
    try:
        from notify import send
    except ImportError:
        print("未找到青龙 notify 模块，仅输出任务日志。")
        return
    try:
        send("九号每日任务汇总", content)
    except Exception as error:
        print(f"青龙通知发送失败：{error}")


def main() -> int:
    try:
        config = TaskConfig.from_environment(os.environ)
    except ConfigurationError as error:
        print(f"配置错误：{error}")
        return 1

    try:
        import requests
    except ImportError:
        print("依赖错误：未安装 requests，请在青龙依赖管理中安装 requests。")
        return 1

    summary = NinebotRunner(config, requests.Session()).run()
    content = summary.render()
    print(content)
    send_notification(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
