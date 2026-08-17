import json
import unittest


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class NinebotTaskTests(unittest.TestCase):
    def test_run_completes_sign_in_and_skips_share_without_payload(self):
        """删除签到请求或把可选分享载荷当成必填时，此测试必须失败。"""
        from ninebot import NinebotRunner, TaskConfig

        session = FakeSession(
            [
                FakeResponse(200, {"code": 0, "msg": "成功"}),
                FakeResponse(200, {"code": 0, "data": {"notOpenedBoxes": []}}),
            ]
        )
        summary = NinebotRunner(TaskConfig("token", "device", None), session).run()

        self.assertEqual(summary.status_for("每日签到"), "成功")
        self.assertEqual(summary.status_for("分享领奖"), "跳过")
        self.assertEqual(summary.status_for("盲盒检查"), "成功")
        self.assertEqual(len(session.calls), 2)

    def test_run_receives_only_receivable_blind_box(self):
        """把可领取判断或 rewardId 请求写错时，此测试必须失败。"""
        from ninebot import NinebotRunner, TaskConfig

        session = FakeSession(
            [
                FakeResponse(200, {"code": 0}),
                FakeResponse(
                    200,
                    {
                        "code": 0,
                        "data": {
                            "notOpenedBoxes": [
                                {"awardDays": 7, "rewardStatus": 1, "blindBoxIds": ["ready"]},
                                {"awardDays": 14, "leftDaysToOpen": 3, "blindBoxIds": ["later"]},
                            ]
                        },
                    },
                ),
                FakeResponse(200, {"code": 0, "data": {"rewardType": 2, "rewardValue": 10}}),
            ]
        )
        summary = NinebotRunner(TaskConfig("token", "device", None), session).run()

        self.assertEqual(summary.status_for("盲盒检查"), "成功")
        self.assertIn("7天盲盒领取成功: N币 10", summary.render())
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(session.calls[2][2]["json"], {"rewardId": "ready"})

    def test_share_api_failure_is_recorded_and_blind_box_still_runs(self):
        """接口业务失败若中止盲盒检查，或未写入汇总时，此测试必须失败。"""
        from ninebot import NinebotRunner, TaskConfig

        session = FakeSession(
            [
                FakeResponse(200, {"code": 0}),
                FakeResponse(200, {"code": 1001, "msg": "分享任务无效"}),
                FakeResponse(200, {"code": 0, "data": {"notOpenedBoxes": []}}),
            ]
        )
        summary = NinebotRunner(TaskConfig("token", "device", json.dumps({"shareId": "x"})), session).run()

        self.assertEqual(summary.status_for("分享领奖"), "失败")
        self.assertIn("分享任务无效", summary.render())
        self.assertEqual(summary.status_for("盲盒检查"), "成功")
        self.assertEqual(len(session.calls), 3)

    def test_share_failure_reports_http_status_business_code_and_message(self):
        """删除业务码或接口提示后，失败日志必须不再满足此测试。"""
        from ninebot import NinebotRunner, TaskConfig

        session = FakeSession(
            [
                FakeResponse(200, {"code": 0}),
                FakeResponse(200, {"code": 1001, "msg": "分享回调已失效"}),
                FakeResponse(200, {"code": 0, "data": {"notOpenedBoxes": []}}),
            ]
        )
        summary = NinebotRunner(TaskConfig("token", "device", json.dumps({"shareId": "x"})), session).run()

        self.assertIn("HTTP 200；业务 code 1001；提示：分享回调已失效", summary.render())

    def test_share_success_claims_reward_before_blind_box_check(self):
        """删除领奖请求、使用错误任务 ID 或颠倒任务顺序时，此测试必须失败。"""
        from ninebot import NinebotRunner, TaskConfig

        session = FakeSession(
            [
                FakeResponse(200, {"code": 0}),
                FakeResponse(200, {"code": 0, "data": {"shared": True}}),
                FakeResponse(200, {"code": 0, "data": {"reward": "N币"}}),
                FakeResponse(200, {"code": 0, "data": {"notOpenedBoxes": []}}),
            ]
        )
        summary = NinebotRunner(TaskConfig("token", "device", json.dumps({"shareId": "x"})), session).run()

        self.assertEqual(summary.status_for("分享领奖"), "成功")
        self.assertEqual(session.calls[2][1], "https://cn-cbu-gateway.ninebot.com/portal/self-service/task/reward")
        self.assertEqual(session.calls[2][2]["json"], {"taskId": "1823622692036079618"})
        self.assertEqual(session.calls[3][0], "GET")

    def test_network_error_is_recorded_and_later_steps_run(self):
        """网络异常若使整个任务崩溃或阻止盲盒检查时，此测试必须失败。"""
        from ninebot import NinebotRunner, TaskConfig

        session = FakeSession(
            [
                OSError("网络断开"),
                FakeResponse(200, {"code": 0, "data": {"notOpenedBoxes": []}}),
            ]
        )
        summary = NinebotRunner(TaskConfig("token", "device", None), session).run()

        self.assertEqual(summary.status_for("每日签到"), "失败")
        self.assertIn("网络断开", summary.render())
        self.assertEqual(summary.status_for("盲盒检查"), "成功")

    def test_missing_required_environment_stops_before_creating_session(self):
        """遗漏必填变量仍构造会话或发请求时，此测试必须失败。"""
        from ninebot import ConfigurationError, TaskConfig

        with self.assertRaisesRegex(ConfigurationError, "NINEBOT_TOKEN"):
            TaskConfig.from_environment({"NINEBOT_DEVICE_ID": "device"})


if __name__ == "__main__":
    unittest.main()
