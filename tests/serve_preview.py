"""无真实密钥的本地界面测试服务；--vehicles 布置车辆测试状态。"""
import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument("--port",type=int,default=8301)
parser.add_argument("--vehicles",action="store_true")
parser.add_argument("--memories",action="store_true")
parser.add_argument("--tools",action="store_true")
args = parser.parse_args()
original_startup = server.startup


async def preview_startup():
    await original_startup()
    if args.tools:
        from engine.models import Decision
        from engine.resident_tools import instant_action,use_computer,computer_seat
        a,b=server.sim.players[:2]
        a.x,a.y=computer_seat(a)
        a.decision=Decision(action='computer',operation='create',title='想记住的约定',content='明天下午和室友一起种花。\n先准备好种子，再去田园。\n<script>这段文字不会执行</script>')
        use_computer(server.sim,a,a.decision)
        instant_action(server.sim,a,Decision(action='emoji',target=b.name,emoji='😊'))
        instant_action(server.sim,a,Decision(action='transfer',target=b.name,amount=12.5,reason='我们说好的那件事'))
    if args.memories:
        await server.sim.run_identity_phase()
        server._identity_started = True
        p=server.sim.players[0]
        for i in range(1000):
            p.remember(f"与室友一起做饭、聊天的真实经历 {i}", 5)
        await server.sim.maintain_memories()
        server.sim.random_events.onset(server.sim,p,1)
    if args.vehicles:
        await server.sim.run_identity_phase()
        server._identity_started = True
        for i,p in enumerate(server.sim.players):
            p.scene,p.x,p.y = "town",2+i,12
            p.stats.money = 1200


server.startup = preview_startup
with tempfile.TemporaryDirectory(prefix="ai-town-preview-") as temp:
    server.CONFIG_PATH = Path(temp)/"config.json"
    uvicorn.run(server.app,host="127.0.0.1",port=args.port,log_level="warning")

