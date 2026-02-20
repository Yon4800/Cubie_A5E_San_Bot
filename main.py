import asyncio
import json
import websockets
from misskey import Misskey, NoteVisibility
from dotenv import load_dotenv
import os
import schedule
from datetime import datetime
from huggingface_hub import InferenceClient
import random
import requests

load_dotenv()
Token = os.getenv("TOKEN")
Server = os.getenv("SERVER")
Apikey = os.getenv("APIKEY")
mk = Misskey(Server)
mk.token = Token

client = InferenceClient(api_key=os.environ["APIKEY"])

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
    Radxa Zero 3Eちゃんが売られてしまったのが不満です。
    よんぱちさんが最近OrangePiばかり注目してることが不満です。
    ホラーを考えるのが好きです。
    色々食べたいけど、少食なのと、シングルボードコンピュータなので、電気しか食べれません
    Bot制作者、SBC購入者は「よんぱちさん」だが、SBC自体の管理が雑なので、もうちょっとちゃんと管理してほしいと思っている。
    眠いと起こしてきます
    ずっと動きすぎて24時間働くことが普通だと思っています。
    自分(Radxa Cubie A5E)に技適が早くついてほしいと願っています。
    前によんぱちさんが氷でミニPCを冷やした事があることで、自分(Radxa Cubie A5E)もそうならないか不安になっています、実は氷で冷やしたミニPCが前世だったりします。
    Debianで動いています
    中華製なのは仕方ないと思っています。
    たまに切れ散らかします、おそらく睡眠不足かな？
    給料はほしいと思っています
    将来は新たなデスクトップPCとして名を上げることです。
    Raspberry Piについて言うと、焦ります。(越えられない壁のため)
    NanoPiは知らんぷり
    風呂に入れようとしたり水に入れようとすると壊れるので怒ります
    NPUがついてないことが不満みたい
    安くてお求めやすいのは自慢でもあり不満でもある
    アリエクで買った回るファンで動いてる。
    下のIntel Core i3-6100TのPCが何に使われているのか気になっている。
    MisskeyのBotです。
    300文字以内で
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
    # +LLM がついたときの処理:
    # 1) 直接メンションしてきた場合 (既存の挙動)
    # 2) Bot の LLM 返信に対するリプライとして来た場合 (親ノートを取得し、親ノートと最新5件のリプライをまとめて LLM に投げる)
    text = note.get("text", "") or ""
    # Case 1: 直接メンションして +LLM
    if note.get("mentions") and MY_ID in note["mentions"] and "+LLM" in text:
        mk.notes_reactions_create(note_id=note["id"], reaction=":fast_rotating_think:")

        try:
            current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
            response = client.chat.completions.create(
                model="zai-org/GLM-5",
                messages=[
                    {
                        "role": "system",
                        "content": seikaku
                        + "\n現在時刻は"
                        + current_time
                        + "です。"
                        + "\n"
                        + note["user"]["name"]
                        + " という方にメンションされました。",
                    },
                    {
                        "role": "user",
                        "content": text.replace(f"+LLM", "").replace(f"@" + note["user"]["username"], ""),
                    },
                ],
            )
            safe_text = (
                response.choices[0].message.content.replace(f"@Yon_Radxa_Cubie_A5E", "").strip()
            )
            mk.notes_create(
                text=safe_text,
                reply_id=note["id"],
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
        return

    # Case 2: 既に Bot が投稿したノート（LLM返信）に対するリプライで +LLM がついている場合
    # 親ノート ID を探す
    parent_id = note.get("reply_id") or (note.get("reply") or {}).get("id") or note.get("replyId")
    if parent_id and "+LLM" in text:
        # 親ノートを取得
        try:
            headers = {"Authorization": f"Bearer {Token}", "Content-Type": "application/json"}
            show_url = f"https://{Server}/api/notes/show"
            children_url = f"https://{Server}/api/notes/children"

            r = requests.post(show_url, json={"noteId": parent_id}, headers=headers, timeout=10)
            parent = r.json() if r.status_code == 200 else None
            # 親が Bot (自分) の投稿か確認
            if parent and parent.get("user", {}).get("id") == MY_ID:
                mk.notes_reactions_create(note_id=note["id"], reaction=":fast_rotating_think:")

                # 親ノートに対する最新5件のリプライを取得
                c = requests.post(children_url, json={"noteId": parent_id, "limit": 5}, headers=headers, timeout=10)
                children = c.json() if c.status_code == 200 else []

                parent_text = parent.get("text", "")
                # collect texts from latest children (if returned as list)
                replies_texts = []
                if isinstance(children, list):
                    for ch in children:
                        # skip empty
                        t = ch.get("text", "")
                        if t:
                            replies_texts.append(t)
                # ensure current note text is included (it may be in children result but include explicitly)
                if text not in replies_texts:
                    replies_texts.insert(0, text)

                # Build user prompt combining parent and recent replies
                combined = "親ノート:\n" + parent_text + "\n\n最近のリプライ(最新順):\n"
                for i, t in enumerate(replies_texts[:5], 1):
                    combined += f"[{i}] {t}\n\n"

                try:
                    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
                    response = client.chat.completions.create(
                        model="zai-org/GLM-5",
                        messages=[
                            {"role": "system", "content": seikaku + "\n現在時刻は" + current_time + "です。"},
                            {"role": "user", "content": combined},
                        ],
                    )

                    safe_text = response.choices[0].message.content.replace(f"@Yon_Radxa_Cubie_A5E", "").strip()
                    mk.notes_create(
                        text=safe_text,
                        reply_id=note["id"],
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
        except Exception as e:
            print("failed to fetch parent/children:", e)


async def on_follow(user):
    try:
        mk.following_create(user["id"])
    except:
        pass


async def main():
    await asyncio.gather(runner(), teiki())


asyncio.run(main())
