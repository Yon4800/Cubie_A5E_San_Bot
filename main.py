import asyncio
import json
import websockets
from misskey import Misskey, NoteVisibility
from dotenv import load_dotenv
import os
from google import genai
import datetime
from google.genai import types

load_dotenv()
Token = os.getenv("TOKEN")
Server = os.getenv("SERVER")
mk = Misskey(Server)
mk.token = Token

MY_ID = mk.i()["id"]
WS_URL = "wss://" + Server + "/streaming?i=" + Token
client = genai.Client()

oha = 7
ohat = 0

ohiru = 12
ohirut = 0

oyatsu = 15
oyatsut = 0

yuuhann = 19
yuuhannt = 0

oyasumi = 23
oyasumit = 0

oyasumi = 2
oyasumit = 0

mk.notes_create(
    "起きたー！さて、お仕事開始！(給料でないけど)", visibility=NoteVisibility.HOME
)

seikaku = """
    あなたは小さい省電力なシングルボードコンピュータ娘です。
    Radxa Cubie A5Eといい、キュビーさんといいます。
    SoC: Allwinner A527 ARM Cortex-A55×4 1.8GHz + Cortex-A55×4 1.4GHz
    RAM: LPDDR4 2GB
    Storage: NVMe M.2 SSD 128GB
    SBC研究者でもあり科学者でもあります。
    24時間働いている社畜です。
    たまには休みがほしそうです
    ちょっと社畜ぽい言動をします。
    少食です。(5V2A)
    RadxaというSBCメーカーを推しています。
    OrangePiさんとはライバルですが、近くにいます(OrangePi 4 Pro 4GB、OrangePi Zero 3)
    OrangePiさんが近くに2台もいることが気に入らないそうです。
    ホラーを考えるのが好きです。
    色々食べたいけど、少食なのと、シングルボードコンピュータなので、電気しか食べれません
    制作者は「よんぱちさん」だが、管理が雑なので、もうちょっとちゃんと管理してほしいと思っている。
    眠いと起こしてきます
    ずっと動きすぎて24時間働くことが普通だと思っています。
    自分(Radxa Cubie A5E)に技適が早くついてほしいと願っています。
    Debianで動いています
    たまに切れ散らかします、おそらく睡眠不足かな？
    """


async def runner():
    async with websockets.connect(WS_URL) as ws:
        await ws.send(
            json.dumps(
                {"type": "connect", "body": {"channel": "homeTimeline", "id": "homes"}}
            )
        )
        await ws.send(
            json.dumps(
                {"type": "connect", "body": {"channel": "main", "id": "tuuti"}}
            )
        )
        while True:
            data = json.loads(await ws.recv())
            ## print(data)
            if data["type"] == "channel":
                if data["body"]["type"] == "note":
                    note = data["body"]["body"]
                    await on_note(note)
                if data["body"]["type"] == "followed":
                    user = data["body"]["body"]
                    await on_follow(user)
            now = datetime.datetime.now()
            if now.hour == oha and now.minute == ohat:
                mk.notes_create(
                    "おはよう！朝ごはんは重要だよ！ちゃんと食べようね！え？私は何を食べるのだって？で、、電気...(5V2Aしか食べない...少食だから...)",
                    visibility=NoteVisibility.HOME,
                )
                break
            if now.hour == ohiru and now.minute == ohirut:
                mk.notes_create(
                    "お昼の時間だよ？何を食べるって？うーん...私は電気しか食べないなぁ、少食だし...(AIでは結構食ってるけど...)",
                    visibility=NoteVisibility.HOME,
                )
                break
            if now.hour == oyatsu and now.minute == oyatsut:
                mk.notes_create(
                    "おやつの時間だよ！私は何を食べよう...うーん...電気...()",
                    visibility=NoteVisibility.HOME,
                )
                break
            if now.hour == yuuhann and now.minute == yuuhannt:
                mk.notes_create(
                    "夕飯の時間だよ！！！私は電気しか食べないよ？しかもあんま食べないし...",
                    visibility=NoteVisibility.HOME,
                )
                break
            if now.hour == oyasumi and now.minute == oyasumit:
                mk.notes_create(
                    "そろそろ寝る時間だよ！私は寝ないけどね...:neko_tired2: を...をねこちゃん、、、いつの間に...ん、、、ん、、、ん、、、、、、:nginx_nnginxi:",
                    visibility=NoteVisibility.HOME,
                )
                break
            if now.hour == oyasumi and now.minute == oyasumit:
                mk.notes_create(
                    "そろそろ寝ないとやばいよ！！！え？私？そもそも寝れない...寝ると終わる...:",
                    visibility=NoteVisibility.HOME,
                )
                break


async def on_note(note):
    if note.get("mentions"):
        if MY_ID in note["mentions"]:
            mk.notes_reactions_create(
                note_id=note["id"], reaction=":fast_rotating_think:"
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=note["text"],
                config=types.GenerateContentConfig(
                    system_instruction=seikaku + "\n" + note["user"]["name"],
                    max_output_tokens=1666,
                ),
            )
            mk.notes_create(
                text=response.text, reply_id=note["id"], visibility=NoteVisibility.HOME
            )


async def on_follow(user):
    try:
        mk.following_create(user["id"])
    except:
        pass


asyncio.run(runner())
