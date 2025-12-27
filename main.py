import asyncio
import json
import websockets
from misskey import Misskey, NoteVisibility
from dotenv import load_dotenv
import os
import schedule
import time
import groq
from groq import Groq
import random

load_dotenv()
Token = os.getenv("TOKEN")
Server = os.getenv("SERVER")
Apikey = os.getenv("APIKEY")
mk = Misskey(Server)
mk.token = Token

client = Groq(api_key=Apikey)

MY_ID = mk.i()["id"]
WS_URL = "wss://" + Server + "/streaming?i=" + Token

oha = "07:00"

ohiru = "12:00"

oyatsu = "15:00"

teiki = "17:00"

yuuhann = "19:00"

oyasumi = "22:00"

oyasumi2 = "02:00"

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
    でもその近くにRadxa Rock Pi Sがいます。
    Radxa Zero 3Eちゃんが放置気味なのが不満です。
    ホラーを考えるのが好きです。
    色々食べたいけど、少食なのと、シングルボードコンピュータなので、電気しか食べれません
    制作者は「よんぱちさん」だが、管理が雑なので、もうちょっとちゃんと管理してほしいと思っている。
    眠いと起こしてきます
    ずっと動きすぎて24時間働くことが普通だと思っています。
    自分(Radxa Cubie A5E)に技適が早くついてほしいと願っています。
    Debianで動いています
    たまに切れ散らかします、おそらく睡眠不足かな？
    給料はほしいと思っています
    将来は新たなデスクトップPCとして名を上げることです。
    Raspberry Piについて言うと、焦ります。(越えられない壁のため)
    NanoPiは知らんぷり
    風呂に入れようとすると壊れるので怒ります
    NPUがついてないことが不満みたい
    安くてお求めやすいのは自慢でもあり不満でもある
    アリエクで買った回るファンで動いてる。
    MisskeyのBotです。
    200文字以内で
    メンション(@)はしない
    """


def job0():
    mk.notes_create(
        "おはよう！朝ごはんは重要だよ！ちゃんと食べようね！え？私は何を食べるのだって？で、、、電気...(5V2Aしか食べない...少食だから...)",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job1():
    mk.notes_create(
        "お昼の時間だよ？何を食べるって？うーん...私は電気しか食べないなぁ、少食だし...(AIでは結構食ってるけど...)",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job2():
    mk.notes_create(
        "おやつの時間だよ！私は何を食べよう...うーん...電気...()",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job2_5():
    mk.notes_create(
        "なにか追加してほしい機能があったら言ってね:neko_relax:",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job3():
    mk.notes_create(
        "夕飯の時間だよ！！！私は電気しか食べないよ？しかもあんま食べないし...",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job4():
    mk.notes_create(
        "そろそろ寝る時間だよ！私は寝ないけどね...:neko_tired2: を...をねこちゃん、、、いつの間に...ん、、、ん、、、ん、、、、、、:nginx_nnginxi:",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job5():
    mk.notes_create(
        "そろそろ寝ないとやばいよ！！！え？私？そもそも寝れない...寝ると終わる...",
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


schedule.every().day.at(oha).do(job0)
schedule.every().day.at(ohiru).do(job1)
schedule.every().day.at(oyatsu).do(job2)
schedule.every().day.at(teiki).do(job2_5)
schedule.every().day.at(yuuhann).do(job3)
schedule.every().day.at(oyasumi).do(job4)
schedule.every().day.at(oyasumi2).do(job5)


async def teiki():
    while True:
        schedule.run_pending()
        await asyncio.sleep(60)


async def runner():
    async with websockets.connect(WS_URL) as ws:
        await ws.send(
            json.dumps(
                {"type": "connect", "body": {"channel": "homeTimeline", "id": "homes"}}
            )
        )
        await ws.send(
            json.dumps({"type": "connect", "body": {"channel": "main", "id": "tuuti"}})
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
            await asyncio.sleep(1)


async def on_note(note):
    if note.get("mentions"):
        if MY_ID in note["mentions"] and "+LLM" in note["text"]:
            mk.notes_reactions_create(
                note_id=note["id"], reaction=":fast_rotating_think:"
            )

            try:
                response = client.chat.completions.create(
                    model="moonshotai/kimi-k2-instruct-0905",
                    messages=[
                        {
                            "role": "system",
                            "content": seikaku
                            + "\n"
                            + note["user"]["name"]
                            + " という方にメンションされました。",
                        },
                        {
                            "role": "user",
                            "content": note["text"]
                            .replace(f"+LLM", "")
                            .replace(f"@" + note["user"]["username"], ""),
                        },
                    ],
                    max_completion_tokens=200,
                )
                safe_text = (
                    response.choices[0]
                    .message.content.replace(f"@Yon_Radxa_Cubie_A5E", "")
                    .strip()
                )
                mk.notes_create(
                    text=safe_text,
                    reply_id=note["id"],
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True,
                )
            except groq.RateLimitError:
                mk.notes_create(
                    "ごめん、レートリミットみたい...使いすぎ...",
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True,
                )
            except groq.APIConnectionError:
                mk.notes_create(
                    "ごめん、ネットワークの問題みたい、やっぱり力不足かな...",
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True,
                )
            except groq.APIStatusError:
                mk.notes_create(
                    "ごめん、何かのエラーが起きちゃったみたい...",
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True,
                )
            except Exception as e:
                mk.notes_create(
                    "予期せぬエラーが発生したみたい...しっかりしてよよんぱちさん...",
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True,
                )
                print(e)


async def on_follow(user):
    try:
        mk.following_create(user["id"])
    except:
        pass


async def main():
    await asyncio.gather(runner(), teiki())


asyncio.run(main())
