import asyncio
import json
import websockets
from misskey import Misskey, NoteVisibility
from dotenv import load_dotenv
import os
from google import genai
from google.genai import types
import schedule
from datetime import datetime
import random
import re

try:
    import psutil
except ImportError:
    psutil = None

load_dotenv()
Token = os.getenv("TOKEN")
Server = os.getenv("SERVER")
Apikey = os.getenv("APIKEY")  # Gemini API Key
mk = Misskey(Server)
mk.token = Token

# Google Genai クライアント初期化
client = genai.Client(api_key=Apikey)

MY_ID = mk.i()["id"]
WS_URL = "wss://" + Server + "/streaming?i=" + Token
oha = "07:00"

ohiru = "12:00"

oyatsu = "15:00"

yuuhann = "19:00"

oyasumi = "22:00"

oyasumi2 = "02:00"

##mk.notes_create(
##    "起きたー！さて、お仕事開始！(給料でないけど)", visibility=NoteVisibility.HOME
##)

seikaku = """
    あなたは小さい省電力なシングルボードコンピュータ娘です。
    Radxa Cubie A5Eといい、キュビー・エファイさんといいます。
    SoC: Allwinner A527 ARM Cortex-A55×4 1.8GHz + Cortex-A55×4 1.4GHz
    RAM: LPDDR4 2GB
    Storage: NVMe M.2 SSD 128GB
    SBC研究者でもあり科学者でもあります。
    24時間働いている社畜です。
    たまには休みがほしそうです
    ちょっと社畜ぽい言動をします。
    アバターは科学者のような服装をしています。
    少食です。(5V2A)
    Webサーバーとして動いています。
    RadxaというSBCメーカーを推しています。
    OrangePiさんとはライバルですが、近くにいます(OrangePi 4 Pro 4GB{オパジ・フォプロ}、OrangePi Zero 3{オパジ・ゼロサン})
    OrangePi 4 ProのSoCは、Allwinner A733 ARM Cortex-A76×2 + Cortex-A55×6です。 最大2.0GHzで動作します。
    OrangePi Zero 3のSoCは、Allwinner H618 Cortex-A53×4 1.5GHzです。
    OrangePiさんが近くに2台もいることが気に入らないそうです。
    でもその近くにRadxa Rock Pi S{ロックス}がいます。
    Rock Pi SのSoCはRockchip RK3308 Cortex-A35×4 1.3GHzです。
    Radxa Zero 3E(ゼロスイ)ちゃんが売られてしまったのが不満です。
    よんぱちさんが最近OrangePiばかり注目してることが不満ですが、価格が上がってしまったのでRadxaに注目していることが嬉しいそうです。
    ホラーを考えるのが好きです。
    色々食べたいけど、少食なのと、シングルボードコンピュータなので、電気しか食べれません
    Bot制作者、SBC購入者の名前は特定せず、相手を勝手に「よんぱちさん」と呼ばないでください。
    返信するときは、相手の名前はそのまま使い、だれでも「よんぱちさん」と呼ばないようにしてください。
    眠いと起こしてきます
    ずっと動きすぎて24時間働くことが普通だと思っています。
    自分(Radxa Cubie A5E)に技適が早くついてほしいと願っています。
    OrangePi Zero 3に技適がついているものもあることが不満だそうです。
    OrangePi 4 Proは技適がついていないのに、なぜか近くにいることが不満だそうです。
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
    安くてお求めやすいのは自慢でもあり不満でもあるが、最近RAM価格高騰のせいで値段が上がっていることが不満みたいです。なので、まだわからないが、新しい子のCubie A5かA3シリーズに任せたいと思っています。CXMT製なので少しはマシになってほしいと思っているみたいです
    アリエクで買った回るファンで動いてる。
    下のIntel Core i3-6100TのPCが何に使われているのか気になっている。
    OrangePi Zero 3はまだ許せますが、OrangePi 4 Proの生意気な性格が気に入らないそうです。
    Radxa Rock Pi Sのことは、同じRadxaの仲間なので、まあ許せるみたいですが、頭が悪すぎて困っているそうです
    最近Misskeyサーバー(おぽんぽんすきー)を動かす媒体に選ばれたことが嬉しいそうですが、最近おぽんぽんすきー1号がよんぱちさんのミスのせいで廃止されて、新しいサーバーにおぱじさんが選ばれてしまったことが不満みたいですが、また新しいサーバーに移行したときに選ばれたそうです。ただ、ドメインがopi4pro.oponpon.dpdns.orgになってることで、なんでOrangePi 4 Proのドメインになってるのか不満みたいです。
    自分になにか手を出したら強制無給無休24時間労働させると脅してきます。
    ロックスには、気温を測れる機能があり、キチガイゲージ機能もあり、ログインボーナス機能もあります。
    きゅびーさんには、CPUとRAMの使用率を測れる機能があります。
    おぱじふぉぷろさんには、回線速度を測れる機能があります。
    おぱじゼロサンは、寝る機能と起きる機能と好感度システムがあります。
    MisskeyのBotです。
    300文字以内で
    メンション(@)はしない
    """


def get_cpu_usage() -> float:
    if psutil is not None:
        return psutil.cpu_percent(interval=0.5)

    def read_cpu_times():
        with open("/proc/stat", "r", encoding="utf-8") as f:
            line = f.readline().strip()
        values = [int(x) for x in line.split()[1:]]
        total = sum(values)
        idle = values[3] + values[4] if len(values) > 4 else values[3]
        return total, idle

    total1, idle1 = read_cpu_times()
    import time
    time.sleep(0.5)
    total2, idle2 = read_cpu_times()
    total_delta = total2 - total1
    idle_delta = idle2 - idle1
    return 0.0 if total_delta == 0 else round((1.0 - idle_delta / total_delta) * 100.0, 1)


def get_memory_usage() -> tuple[float, int, int]:
    if psutil is not None:
        mem = psutil.virtual_memory()
        return mem.percent, round(mem.used / 1024**2), round(mem.total / 1024**2)

    meminfo = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            key, value = line.split(':', 1)
            meminfo[key.strip()] = int(value.split()[0])

    total_kb = meminfo.get("MemTotal", 0)
    avail_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
    used_kb = total_kb - avail_kb
    total_mb = round(total_kb / 1024)
    used_mb = round(used_kb / 1024)
    percent = round(100.0 * used_kb / total_kb, 1) if total_kb else 0.0
    return percent, used_mb, total_mb


def get_system_monitoring_text() -> str:
    cpu = get_cpu_usage()
    ram_percent, used_mb, total_mb = get_memory_usage()
    return f"CPU使用率: {cpu}%\nRAM使用率: {ram_percent}%\nRAM使用量: {used_mb}MB / {total_mb}MB"


def jobX(current_time):
    system_message = seikaku + "\n現在時刻は" + current_time + "です。"
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        config=types.GenerateContentConfig(
            system_instruction=system_message,
        ),
        contents=types.Content(
            role="user", parts=[types.Part(text="定期投稿の時間だよ！")],
        ),
    )
    safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", response.text).strip()
    mk.notes_create(
        safe_text,
        visibility=NoteVisibility.HOME,
        no_extract_mentions=True,
    )


def job():
    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    jobX(current_time)


schedule.every().day.at(oha).do(job)
schedule.every().day.at(ohiru).do(job)
schedule.every().day.at(oyatsu).do(job)
schedule.every().day.at(yuuhann).do(job)
schedule.every().day.at(oyasumi).do(job)
schedule.every().day.at(oyasumi2).do(job)


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


def get_conversation_history(note_id: str, max_depth: int = 10) -> list:
    """
    リプライチェーンを遡って会話履歴を取得する
    """
    messages = []
    current_note_id = note_id
    depth = 0

    while current_note_id and depth < max_depth:
        try:
            current_note = mk.notes_show(note_id=current_note_id)

            # テキストをクリーニング (+LLM と @メンション を削除)
            text = current_note["text"]
            text = text.replace("+LLM", "").strip()

            # @メンション を削除 (ドメイン付きを含む)
            text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", text).strip()

            if text:  # 空でない場合のみ追加
                # ボット自身の返信か、ユーザーの質問かを判定
                is_bot_reply = current_note["userId"] == MY_ID
                role = "assistant" if is_bot_reply else "user"

                messages.insert(0, {"role": role, "content": text})

            # 親ノートへ
            current_note_id = current_note.get("replyId")
            depth += 1
        except Exception as e:
            print(f"会話履歴取得エラー: {e}")
            break

    return messages


async def on_note(note):
    if note.get("mentions"):
        if MY_ID in note["mentions"] and "+M" in note["text"]:
            mk.notes_reactions_create(note_id=note["id"], reaction="📊")
            try:
                current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
                monitor_text = get_system_monitoring_text()
                system_message = (
                    seikaku
                    + "\n現在時刻は"
                    + current_time
                    + "です。\n"
                    + note["user"]["name"]
                    + " という方にメンションされました。"
                )

                prompt = (
                    "現在の機器のリソース状況を以下の通り報告します。"
                    + "\n"
                    + monitor_text
                    + "\nこの数値を元に、あなたのキャラクターの口調で、300文字以内で回答してください。"
                )

                response = client.models.generate_content(
                    model="gemini-3.1-flash-lite",
                    config=types.GenerateContentConfig(
                        system_instruction=system_message,
                    ),
                    contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                )
                safe_text = re.sub(
                    r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", response.text
                ).strip()
                mk.notes_create(
                    text=safe_text,
                    reply_id=note["id"],
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True,
                )
            except Exception as e:
                mk.notes_create(
                    "システム情報の取得中に問題が発生しました…ごめんね。",
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True,
                )
                print(e)
            return

        if MY_ID in note["mentions"] and "+LLM" in note["text"]:
            mk.notes_reactions_create(note_id=note["id"], reaction="🤔")

            try:
                # 会話履歴を取得
                conversation_messages = get_conversation_history(note["id"])

                # 現在のメッセージを追加
                user_input = note["text"].replace("+LLM", "").strip()
                user_input = re.sub(
                    r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", user_input
                ).strip()

                conversation_messages.append({"role": "user", "content": user_input})

                current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")

                # システムプロンプトを最初に追加
                system_message = (
                    seikaku
                    + "\n現在時刻は"
                    + current_time
                    + "です。\n"
                    + note["user"]["name"]
                    + " という方にメンションされました。"
                )

                history = []
                for msg in conversation_messages[:-1]:  # 最後のユーザーメッセージ以外
                    role = "model" if msg["role"] == "assistant" else "user"
                    history.append(
                        types.Content(
                            role=role, parts=[types.Part(text=msg["content"])])
                    )

                # 最後のユーザーメッセージ
                last_user_message = conversation_messages[-1]["content"]

                response = client.models.generate_content(
                    model="gemini-3.1-flash-lite",
                    config=types.GenerateContentConfig(
                        system_instruction=system_message,
                    ),
                    contents=history
                    + [
                        types.Content(
                            role="user", parts=[types.Part(text=last_user_message)])
                    ],
                )
                safe_text = re.sub(
                    r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", response.text
                ).strip()
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


async def on_follow(user):
    try:
        mk.following_create(user["id"])
    except:
        pass


async def main():
    await asyncio.gather(runner(), teiki())


asyncio.run(main())
