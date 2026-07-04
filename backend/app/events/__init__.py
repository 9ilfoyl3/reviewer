"""事件桥接层。

定义 Progress_Event / EventType，Worker 侧 ReviewEventBus 发布到
Redis Pub/Sub，API 侧 EventBridge 订阅并转发为 SSE。
"""
