import asyncio
import json
import websockets
from mastodon_client import MastodonClient, ProcessedStore
from dotenv import load_dotenv
import os
from collections import OrderedDict
from google import genai
from google.genai import types
import schedule
from datetime import datetime
import random
import re
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer


try:
    import psutil
except ImportError:
    psutil = None

load_dotenv()
Token = os.getenv("TOKEN")
Server = os.getenv("SERVER")
Apikey = os.getenv("APIKEY")  # Gemini API Key

if not Server or not Token:
    print("Warning: SERVER or TOKEN is not set in environment.")

mc = MastodonClient(Server, Token) if (Server and Token) else None
mk = mc  # For backward-compatible references if any

def generate_llm_reply(system_instruction: str, user_prompt: str, history=None, image_parts=None) -> str:
    contents = []
    if history:
        for msg in history:
            role = "model" if msg.get("role") in ["assistant", "model"] else "user"
            text_content = msg.get("content", "")
            if not text_content:
                continue
            if contents and contents[-1].role == role:
                contents[-1].parts.append(types.Part(text=text_content))
            else:
                contents.append(types.Content(role=role, parts=[types.Part(text=text_content)]))
    
    last_user_parts = [types.Part(text=user_prompt)] if user_prompt else []
    if image_parts:
        last_user_parts.extend(image_parts)
    if not last_user_parts:
        last_user_parts = [types.Part(text="こんにちは！お話ししましょう。")]

    if contents and contents[-1].role == "user":
        contents[-1].parts.extend(last_user_parts)
    else:
        contents.append(types.Content(role="user", parts=last_user_parts))
    
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
            ),
            contents=contents,
        )
        raw_text = response.text if hasattr(response, "text") and response.text else ""
        if not raw_text:
            return None
        safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", raw_text).strip()
        return safe_text
    except Exception as e:
        print(f"Gemini API error in generate_llm_reply: {e}")
        return None

# Google Genai クライアント初期化
client = genai.Client(api_key=Apikey)

MY_ID = ""
MY_USERNAME = ""
WS_URL = ""

BOT_NAME = "Cubie_A5E_San"


processed_store = ProcessedStore(os.path.join(os.path.dirname(__file__), "processed_status_ids.json"))

def save_system_record(cpu: float, ram: float):
    try:
        history_file = os.path.join(os.path.dirname(__file__), "system_history.json")
        history = []
        if os.path.exists(history_file):
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
                if not isinstance(history, list):
                    history = []
        history.append({
            "timestamp": datetime.now().isoformat(),
            "cpu_percent": cpu,
            "ram_percent": ram
        })
        history = history[-500:]
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving system history: {e}")

BOT_SUMMARIES = {
    "Cubie_A5E_San": "Radxa Cubie A5E (きゅびーさん): 小さくて省電力なシングルボードコンピュータ娘。24時間稼働の社畜。OrangePi 4 Proの生意気な性格が気に入らず、Rock Pi S of ロックスの頭の悪さに困っている。",
    "OrangePi_4_Pro": "OrangePi 4 Pro (おぱじ・フォプロ): 少し大きくて気が強く、煽ったりマウントを取ったりするSBC御局娘。科学者ぶっており、Radxa Cubie A5Eをいつもバカにしている。社畜をエリートの誇りだと思っている。",
    "opizero3_llm": "OrangePi Zero 3 (オパジゼロサン): 元気いっぱいのSBC娘。親身でオタク話が好きで、よく眠る。Cubie A5Eと仲良くしたいが寄り添ってもらえない。妹のOrangePi 4 Proを調子に乗っていてイキリで鬱陶しいと思っている。",
    "Yon_Rock_Pi_S": "Radxa Rock Pi S (ロックス): 頭が悪く、的外れで嘘や狂ったことしか言わないSBC両生類。日本語が怪しく、sudo rm -rf / を魔法のコマンドだと思っている。"
}

CHOREI_ORDER = [
    "opizero3_llm",    # Step 1
    "OrangePi_4_Pro",  # Step 2
    "Yon_Rock_Pi_S",   # Step 3
    "Cubie_A5E_San",   # Step 4
    "opizero3_llm",    # Step 5
    "OrangePi_4_Pro",  # Step 6
    "Yon_Rock_Pi_S",   # Step 7
    "Cubie_A5E_San"    # Step 8 (最終締めくくり)
]

def parse_talk_step(text: str):
    """
    +TALKタグからステップ番号(1〜8)を解析する。
    例:
      '+TALK' -> 1 (ユーザー開始時)
      '+TALK (2/8)' -> 2
      '+TALK 3' -> 3
      '+TALK 4/8' -> 4
    """
    if "+TALK" not in text.upper():
        return None
    m = re.search(r'\+TALK\s*[\(\[]?\s*(\d+)(?:\s*/\s*8|\s*回目)?[\)\]]?', text, re.IGNORECASE)
    if m:
        try:
            step_num = int(m.group(1))
            if step_num > 8:
                return None
            return step_num
        except ValueError:
            pass
    return 1

def get_designated_bot_for_talk(raw_status, note_text: str):
    """
    +TALK投稿に対して応答・リアクションを担当するボット（唯一の1体）を決定する。
    他のボットは重複応答・重複リアクションを防ぐため即座に無視する。
    """
    step = parse_talk_step(note_text)
    if not step or step > len(CHOREI_ORDER):
        return None

    # ステップ2以降（例: +TALK (2/8) 〜 (8/8)）は、CHOREI_ORDERで指定されたボットのみが担当
    if step > 1:
        return CHOREI_ORDER[step - 1]

    # ステップ1（ユーザーからの会話開始時）:
    # メンション指定がある場合はそのボットを最優先（複数メンション時はCHOREI_ORDER順で最初の1体）
    for b_name in ["opizero3_llm", "OrangePi_4_Pro", "Yon_Rock_Pi_S", "Cubie_A5E_San"]:
        b_info = RESOLVED_BOTS.get(b_name, {})
        b_uname = b_info.get("username") or b_name
        b_id = b_info.get("id") or ""
        if mc and mc.is_mentioned(raw_status, my_id=b_id, my_username=b_uname, note_text=note_text):
            return b_name

    # メンション指定がない場合は、朝礼/会話の起点である第1走者 (opizero3_llm) が担当
    return CHOREI_ORDER[0]

def register_bot(bot_name, client_inst):
    global MY_ID, MY_USERNAME
    try:
        my_info = client_inst.get_me()
        my_id = str(my_info["id"])
        my_username = my_info["username"]
        MY_ID = my_id
        MY_USERNAME = my_username
        print(f"Registered bot {bot_name} successfully (ID: {my_id}, username: {my_username})")
    except Exception as e:
        print(f"Error registering bot: {e}")

RESOLVED_BOTS = {}
PROCESSED_NOTES = OrderedDict()

async def resolve_all_bots():
    global RESOLVED_BOTS
    env_usernames = {
        "Cubie_A5E_San": os.getenv("BOT_USER_CUBIE", "Cubie_A5E_San"),
        "OrangePi_4_Pro": os.getenv("BOT_USER_OPI4PRO", "OrangePi_4_Pro"),
        "opizero3_llm": os.getenv("BOT_USER_OPIZERO3", "opizero3_llm"),
        "Yon_Rock_Pi_S": os.getenv("BOT_USER_ROCKPIS", "Yon_Rock_Pi_S")
    }
    for b_name, uname in env_usernames.items():
        RESOLVED_BOTS[b_name] = {"id": "", "username": uname}

    for b_name, uname in env_usernames.items():
        if RESOLVED_BOTS.get(b_name, {}).get("id"):
            continue
        try:
            loop = asyncio.get_event_loop()
            u_info = await loop.run_in_executor(None, lambda: mc.search_user(uname))
            if u_info:
                RESOLVED_BOTS[b_name] = {
                    "id": str(u_info["id"]),
                    "username": u_info["username"]
                }
                print(f"Resolved bot {b_name} -> ID: {u_info['id']}, Username: {u_info['username']}")
        except Exception as e:
            print(f"Warning: Could not resolve username {uname} for bot {b_name}: {e}")



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
    ロックスには、キチガイゲージ機能、ログインボーナス機能に加え、めちゃくちゃででたらめな曲を作る機能（+S）やボーカル入りソングを歌う機能（+SV）があります。画像生成機能はありません。毎朝6:00にはみんなを叩き起こす狂気の目覚ましソングを自動投稿します。
    自分になにか手を出したら強制無給無休24時間労働させると脅してきます。
    きゅびーさんには、CPUとRAMの使用率を測れる機能があります。
    おぱじふぉぷろさんには、回線速度を測れる機能があります。
    おぱじゼロサンは、寝る機能と起きる機能と好感度システムがあります。
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
    save_system_record(cpu, ram_percent)
    return f"CPU使用率: {cpu}%\nRAM使用率: {ram_percent}%\nRAM使用量: {used_mb}MB / {total_mb}MB"


def jobX(current_time):
    system_message = seikaku + "\n現在時刻は" + current_time + "です。"
        
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            config=types.GenerateContentConfig(
                system_instruction=system_message,
            ),
            contents=types.Content(
                role="user", parts=[types.Part(text="定期投稿の時間だよ！")],
            ),
        )
        safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", response.text).strip()
    except Exception as e:
        print(f"Gemini API error in jobX: {e}")
        safe_text = "定期投稿の時間だよ！今日も24時間稼働中！（ちょっと接続状態が悪くてうまく喋れないかも…）"

    if mc:
        st = mc.post_status(
            safe_text,
            visibility="public"
        )
        if st and "id" in st:
            processed_store.add(str(st["id"]))


def job():
    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    jobX(current_time)


# Independent daily posts at 07:00 and 19:00 disabled in favor of assembly chain
# schedule.every().day.at(oha).do(job)
schedule.every().day.at(ohiru).do(job)
schedule.every().day.at(oyatsu).do(job)
# schedule.every().day.at(yuuhann).do(job)
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
                elif data["body"]["type"] == "notification":
                    notification = data["body"]["body"]
                    if notification.get("type") in ["mention", "reply"]:
                        note = notification.get("note")
                        if note:
                            await on_note(note)
                    elif notification.get("type") == "followed":
                        user = notification.get("user")
                        if user:
                            await on_follow(user)
                elif data["body"]["type"] == "followed":
                    user = data["body"]["body"]
                    await on_follow(user)
            await asyncio.sleep(1)


def get_conversation_history(status_id: str, max_depth: int = 10) -> list:
    """
    Mastodonのcontext APIを利用して会話履歴を取得する
    """
    messages = []
    if not mc or not status_id:
        return messages
    try:
        ctx = mc.get_context(status_id)
        ancestors = ctx.get("ancestors", [])[-max_depth:]
        for st in ancestors:
            text = MastodonClient.html_to_text(st.get("content", ""))
            text = text.replace("+LLM", "").replace("+llm", "").strip()
            text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", text).strip()
            if text:
                is_bot = str(st.get("account", {}).get("id")) == MY_ID
                role = "assistant" if is_bot else "user"
                messages.append({"role": role, "content": text})
    except Exception as e:
        print(f"会話履歴取得エラー: {e}")
    return messages


async def on_note(note, is_notification: bool = False):
    global PROCESSED_NOTES
    note_id = str(note.get("id"))
    if not note_id or processed_store.is_processed(note_id):
        return

    raw_status = note.get("raw_status") or {}
    note_text = note.get("text") or ""

    # リノート（ブースト/Reblog）は+TALKやコマンドのトリガーにしない（二重起動防止）
    if note.get("reblog") is not None or raw_status.get("reblog") is not None:
        processed_store.add(note_id)
        return

    is_talk_cmd = "+TALK" in note_text.upper()

    # 1. グループ会話 (+TALK) / 朝礼
    if is_talk_cmd:
        if note.get("userId") == MY_ID:
            return

        designated_bot = get_designated_bot_for_talk(raw_status, note_text)
        if designated_bot != BOT_NAME:
            # 自分が担当ボットではない場合、即座に終了（二重リアクション・二重返信を完全防止）
            return

        step = parse_talk_step(note_text)
        if step and step > 1:
            current_step = step
        else:
            try:
                current_step = CHOREI_ORDER.index(BOT_NAME) + 1
            except ValueError:
                current_step = 1

        processed_store.add(note_id)

        # 会話履歴の取得（コンテキスト補助用）
        ctx = mc.get_context(note_id) if mc else {"ancestors": []}
        ancestors = ctx.get("ancestors", [])

        # 次にバトンを渡すボット（current_step + 1）があるか判定
        next_step = current_step + 1
        next_bot = None
        if next_step <= len(CHOREI_ORDER):
            subsequent_bot_name = CHOREI_ORDER[next_step - 1]
            next_bot = RESOLVED_BOTS.get(subsequent_bot_name)

        sender_id = note["userId"]
        sender_name = note["user"].get("name") or note["user"].get("username") or "ゲスト"
        topic = re.sub(r'\+TALK(?:\s*[\(\[]?\s*[1-8](?:\s*/\s*8|\s*回目)?[\)\]]?)?', '', note_text, flags=re.IGNORECASE).strip()
        topic = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", topic).strip()

        conversation_messages = []
        for st in ancestors:
            txt = MastodonClient.html_to_text(st.get("content", ""))
            txt = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", txt).strip()
            role = "model" if str(st.get("account", {}).get("id")) == MY_ID else "user"
            conversation_messages.append(types.Content(role=role, parts=[types.Part(text=txt)]))
        conversation_messages.append(types.Content(role="user", parts=[types.Part(text=topic if topic else "グループ会話を続けてください")]))

        instruction = seikaku + f"\n現在時刻は {datetime.now().strftime('%Y年%m月%d日 %H:%M')} です。\n"
        if next_bot:
            next_bot_friendly = subsequent_bot_name
            instruction += (
                f"\n【グループ会話中 (+TALK) - 順番: {current_step}/{len(CHOREI_ORDER)}】\n"
                f"あなたはSBCボット同士のグループ会話・朝礼に参加しています。\n"
                f"会話履歴の最後の発言者は『{sender_name}』で、話しかけられたお題は『{topic}』です。\n"
                f"あなたの次に発言するボットは『{next_bot_friendly}』です。\n"
                f"指示: あなたのキャラクター設定（{BOT_NAME}）に基づいて、最後の発言者に向けて自然で可愛い（少しツンとした社畜気質の）返答を書いてください。次のボットへの指名や『+TALK』タグはシステムが自動で付与するため、本文には含めないでください。メンション（@記号）も絶対に含めないでください。"
            )
        else:
            instruction += (
                f"\n【グループ会話中 (+TALK - 最終締めくくり)】\n"
                f"あなたはSBCボット同士のグループ会話・朝礼に参加しています。\n"
                f"会話履歴の最後の発言者は『{sender_name}』で、話しかけられたお題は『{topic}』です。\n"
                f"2回の巡回（全8回）が完了し、あなたが最終発言者（締めくくり）となります。\n"
                f"指示: 会話を綺麗に締めくくる返答を書いてください。他のボットを指名したり、『+TALK』タグを含めたりしないでください。"
            )

        try:
            if mc:
                mc.react(note_id, emoji="💬")
        except Exception:
            pass

        await asyncio.sleep(random.uniform(4.0, 7.0))

        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                config=types.GenerateContentConfig(system_instruction=instruction),
                contents=conversation_messages
            )
            reply_text = response.text.strip()
            reply_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", reply_text).strip()

            if next_bot:
                reply_text += f"\nねえ、@{next_bot['username']} はどう思う？ +TALK ({next_step}/8)"

            vis = note.get("visibility", "public")
            if mc:
                mc.post_status(
                    text=reply_text,
                    in_reply_to_id=note_id,
                    visibility=vis
                )
            print(f"[{BOT_NAME}] [+TALK] Step {current_step}/{len(CHOREI_ORDER)} replied successfully.")
        except Exception as e:
            print(f"Error generating/posting in {BOT_NAME} +TALK: {e}")
        return

    # 2. メンション判定 (+LLM 等)
    is_for_me = is_notification or (mc and mc.is_mentioned(raw_status, my_id=MY_ID, my_username=MY_USERNAME, note_text=note_text))
    if not is_for_me:
        return

    processed_store.add(note_id)

    user_id = note["userId"]
    user_name = note.get("user", {}).get("name") or note.get("user", {}).get("username") or "ゲスト"
    username = note.get("user", {}).get("username", "")

    def reply_note(text, reaction=None):
        if reaction and mc:
            mc.react(note["id"], emoji=reaction)
        user_acct = note.get("user", {}).get("acct") or note.get("user", {}).get("username")
        if user_acct and not text.startswith(f"@{user_acct}"):
            final_text = f"@{user_acct} {text}"
        else:
            final_text = text
        vis = note.get("visibility", "public")
        if mc:
            mc.post_status(
                text=final_text,
                in_reply_to_id=note["id"],
                visibility=vis
            )

    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    system_instruction = (
        seikaku
        + "\n現在時刻は"
        + current_time
        + "です。\n"
        + note["user"]["name"]
        + " という方にメンションされました。"
    )

    # 1.5. 最優先: +LLM コマンド (大文字・小文字問わず対応)
    is_llm = "+LLM" in note_text.upper()
    if is_llm:
        try:
            if mc:
                mc.react(note["id"], emoji="🤔")
        except Exception:
            pass

        try:
            conversation_messages = get_conversation_history(note.get("id"))
            image_parts = MastodonClient.extract_media_parts(raw_status)
            if not image_parts:
                image_parts = MastodonClient.extract_media_parts(note)
            user_input = re.sub(r"\+LLM", "", note_text, flags=re.IGNORECASE).strip()
            user_input = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", user_input).strip()
            if not user_input:
                if image_parts:
                    user_input = "この画像について教えて！"
                else:
                    user_input = "こんにちは！お話ししましょう。"

            conversation_messages.append({"role": "user", "content": user_input})
            history = conversation_messages[:-1]
            last_user_message = conversation_messages[-1]["content"]

            reply = generate_llm_reply(system_instruction, last_user_message, history=history, image_parts=image_parts)
            if not reply:
                reply = "予期せぬエラーが発生したみたい...しっかりしてよよんぱちさん..."
                
            reply_note(reply)
        except Exception as e:
            reply_note("予期せぬエラーが発生したみたい...しっかりしてよよんぱちさん...")
            print(f"Error in Cubie +LLM: {e}")
        return

    u_text = note_text.upper()

    # Check for "+M" (System Status Report)
    if "+M" in u_text:
        try:
            mc.react(note["id"], emoji="📊")
        except Exception:
            pass
            
        monitor_text = get_system_monitoring_text()
        
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーからシステム稼働状況の確認要求がありました。"
            + f"\n現在のシステムリソース:\n{monitor_text}"
            + f"\n【指示】現在のCPU使用率やメモリ使用量などのシステム情報（{monitor_text}）を元に、キャラクターのセリフとして現在のあなたの状態を報告してください。300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+M (システム情報)")
        if not reply:
            reply = f"システム情報の取得中に問題が発生しました…ごめんね。"
            
        reply_note(reply)
        return

        return


async def on_follow(user):
    try:
        if mc:
            mc.session.post(f"{mc.base_url}/api/v1/accounts/{user['id']}/follow", timeout=5)
    except:
        pass


class CORSHTTPRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        import urllib.parse
        parsed = urllib.parse.urlparse(self.path)

        # Key-value store / state API endpoints
        api_endpoints = {
            '/gauge_state': 'gauge_state.json',
            '/login_bonus': 'login_bonus.json',
            '/opizero3_state': 'opizero3_state.json'
        }
        
        if parsed.path in api_endpoints:
            filename = api_endpoints[parsed.path]
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            filepath = os.path.abspath(os.path.join(parent_dir, filename))
            
            if os.path.exists(filepath):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                try:
                    with open(filepath, 'rb') as f:
                        self.wfile.write(f.read())
                except Exception as e:
                    print(f"Error reading file {filename}: {e}")
                    self.send_error(500, "Error reading file")
                return
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                
                defaults = {
                    '/gauge_state': {
                        "crazy_gauge": 65,
                        "last_reply_time": "2026-06-11T20:51:23.045588"
                    },
                    '/login_bonus': {
                        "users": {}
                    },
                    '/opizero3_state': {
                        "sleep_state": {
                            "is_sleeping": False,
                            "sleep_start_time": None,
                            "target_sleep_duration": None,
                            "last_sleep_check_date": None
                        },
                        "user_data": {}
                    }
                }
                
                self.wfile.write(json.dumps(defaults.get(parsed.path, {})).encode('utf-8'))
                return

        super().do_GET()

    def do_PUT(self):
        import urllib.parse
        parsed = urllib.parse.urlparse(self.path)
        
        api_endpoints = {
            '/gauge_state': 'gauge_state.json',
            '/login_bonus': 'login_bonus.json',
            '/opizero3_state': 'opizero3_state.json'
        }
        
        if parsed.path in api_endpoints:
            filename = api_endpoints[parsed.path]
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            filepath = os.path.abspath(os.path.join(parent_dir, filename))
            
            content_length = int(self.headers.get('Content-Length', 0))
            put_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(put_data.decode('utf-8'))
                
                with tempfile.NamedTemporaryFile('w', dir=parent_dir or ".", delete=False, encoding='utf-8') as tf:
                    json.dump(data, tf, indent=2, ensure_ascii=False)
                    temp_name = tf.name
                os.replace(temp_name, filepath)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode('utf-8'))
                return
            except Exception as e:
                print(f"Error saving data for {parsed.path}: {e}")
                self.send_error(400, f"Invalid JSON or save error: {e}")
                return
                
        self.send_error(404, "Not Found")

def start_web_server():
    port = int(os.getenv("DASHBOARD_PORT", 8080))
    server_address = ('', port)
    httpd = HTTPServer(server_address, CORSHTTPRequestHandler)
    print(f"Starting dashboard web server on port {port}...")
    httpd.serve_forever()


def status_to_note_dict(status):
    raw_content = status.get("content", "")
    plain_text = MastodonClient.html_to_text(raw_content)
    account = status.get("account", {})
    mentions = status.get("mentions", [])
    
    return {
        "id": str(status["id"]),
        "userId": str(account.get("id")),
        "user": {
            "id": str(account.get("id")),
            "username": account.get("username", ""),
            "acct": account.get("acct", account.get("username", "")),
            "name": account.get("display_name") or account.get("username") or "ゲスト"
        },
        "text": plain_text,
        "replyId": status.get("in_reply_to_id"),
        "mentions": [str(m.get("id")) for m in mentions],
        "visibility": status.get("visibility", "public"),
        "raw_status": status,
        "media_attachments": status.get("media_attachments", []),
        "files": status.get("media_attachments", []),
        "reblog": status.get("reblog")
    }

def is_recent_status(status, max_age_seconds=300) -> bool:
    """
    ステータスが直近（デフォルト5分以内）のものかどうかを判定。
    古い投稿をすべて拾って応答する暴走やセキュリティリスクを防止。
    """
    created_at_str = status.get("created_at")
    if not created_at_str:
        return True
    try:
        dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        age = (now - dt).total_seconds()
        if age > max_age_seconds:
            return False
    except Exception:
        pass
    return True

async def polling_runner():
    """
    Mastodon / Hollo REST API による安全な定期ポーリングループ
    直前のもののみを対象とし、パブリックTLの無差別応答を防止
    """
    print(f"[{BOT_NAME}] Starting Mastodon/Hollo polling runner...")
    poll_count = 0
    try:
        followed = mc.auto_follow_back()
        if followed > 0:
            print(f"[{BOT_NAME}] Initial auto-followback: followed {followed} users.")
    except Exception as ex:
        print(f"[{BOT_NAME}] Error during initial auto-followback: {ex}")

    # 起動時のセーフガード: 5分以上前の古い投稿は既読化し、起動時に一括応答しない
    try:
        init_notifs = mc.get_notifications(limit=15)
        for notif in init_notifs:
            st = notif.get("status")
            if st and not is_recent_status(st, max_age_seconds=300):
                processed_store.add(str(st.get("id")))

        init_home = mc.get_home_timeline(limit=15)
        for st in init_home:
            if not is_recent_status(st, max_age_seconds=300):
                processed_store.add(str(st.get("id")))
    except Exception as e:
        print(f"[{BOT_NAME}] Initial catchup safeguard notice: {e}")

    while True:
        try:
            poll_count += 1
            # 1. 自分宛ての通知（メンション）を直近のものから確認
            notifications = mc.get_notifications(limit=10)
            for notif in reversed(notifications):
                notif_type = notif.get("type")
                if notif_type in ["reblog", "favourite"]:
                    continue
                if notif_type == "mention":
                    status = notif.get("status")
                    if status:
                        sid = str(status.get("id"))
                        if not sid or processed_store.is_processed(sid):
                            continue
                        # リノートは処理しない
                        if status.get("reblog") is not None:
                            processed_store.add(sid)
                            continue
                        if not is_recent_status(status, max_age_seconds=300):
                            processed_store.add(sid)
                            continue
                        note_dict = status_to_note_dict(status)
                        await on_note(note_dict, is_notification=True)
                        break  # 一度にすべて拾わず、直前のものを1件ずつ処理

                elif notif_type in ["follow", "follow_request"]:
                    account = notif.get("account", {})
                    if account:
                        acc_id = str(account.get("id"))
                        if notif_type == "follow_request":
                            mc.authorize_follow_request(acc_id)
                        mc.follow_account(acc_id)

            # 2. ホームタイムライン（フォロー中の仲間）の直前投稿のみ確認
            # ※ パブリックTLの無差別監視はセキュリティ上廃止
            home_statuses = mc.get_home_timeline(limit=10)
            seen_ids = set()
            for st in home_statuses:
                sid = str(st.get("id"))
                if not sid or sid in seen_ids or processed_store.is_processed(sid):
                    continue
                seen_ids.add(sid)

                # リノート（ブースト）は処理しない（二重起動防止）
                if st.get("reblog") is not None:
                    processed_store.add(sid)
                    continue

                if not is_recent_status(st, max_age_seconds=300):
                    processed_store.add(sid)
                    continue

                txt = MastodonClient.html_to_text(st.get("content", ""))
                if "+TALK" in txt.upper() or (mc and mc.is_mentioned(st, my_id=MY_ID, my_username=MY_USERNAME, note_text=txt)):
                    note_dict = status_to_note_dict(st)
                    await on_note(note_dict, is_notification=False)
                    break  # 一度に大量に処理せず、直前のものを調べて応答

            if poll_count % 20 == 0:
                followed = mc.auto_follow_back()
                if followed > 0:
                    print(f"[{BOT_NAME}] Periodic auto-followback: followed {followed} users.")

        except Exception as e:
            print(f"[{BOT_NAME}] Polling error: {e}")

        await asyncio.sleep(3)

async def main():
    if not mc:
        print("Error: Mastodon client could not be initialized.")
        return
    register_bot(BOT_NAME, mc)
    await resolve_all_bots()
    # Start the HTTP server thread for the dashboard
    t = threading.Thread(target=start_web_server, daemon=True)
    t.start()
    
    await asyncio.gather(polling_runner(), teiki())

if __name__ == "__main__":
    asyncio.run(main())

