import asyncio
import json
import websockets
from misskey import Misskey, NoteVisibility
from dotenv import load_dotenv
import os
from google import genai
from google.genai import types
import schedule
from datetime import datetime, timedelta
import random
import re
import tempfile
import requests
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
mk = Misskey(Server)
mk.token = Token

from shared_economy_helper import load_economy, save_economy, get_user_state, update_exchange_rates, get_recent_rates_history_desc, apply_rate_change

def get_bot_state(data, bot_name="Cubie_A5E_San"):
    is_modified = False
    if bot_name not in data["bots"]:
        initial_paid_time = (datetime.now() - timedelta(days=1)).isoformat()
        data["bots"][bot_name] = {
            "balance_cbc": 0.0,
            "last_salary_paid_time": initial_paid_time,
            "break_until": None,
            "virtual_pc_count": 0,
            "items": []
        }
        is_modified = True
    bot_data = data["bots"][bot_name]
    # Ensure fields exist
    if "balance_cbc" not in bot_data:
        bot_data["balance_cbc"] = 0.0
        is_modified = True
    if "last_salary_paid_time" not in bot_data:
        bot_data["last_salary_paid_time"] = (datetime.now() - timedelta(days=1)).isoformat()
        is_modified = True
    if "break_until" not in bot_data:
        bot_data["break_until"] = None
        is_modified = True
    if "virtual_pc_count" not in bot_data:
        bot_data["virtual_pc_count"] = 0
        is_modified = True
    if "items" not in bot_data:
        bot_data["items"] = []
        is_modified = True
        
    if is_modified:
        save_economy(data)
        
    return bot_data

def is_bot_on_break(bot_data):
    if bot_data.get("break_until"):
        try:
            break_until = datetime.fromisoformat(bot_data["break_until"])
            if datetime.now() < break_until:
                return True
        except Exception:
            pass
    return False

def background_update_rates():
    data = load_economy()
    update_exchange_rates(data, datetime.now())
    save_economy(data)
    print("Exchange rates updated in background.")

def get_rate_status_description(rate: float) -> str:
    if rate < 100.0:
        return f"通貨高（1 $SBC = {rate:.2f} CBC/OGC であり、基準の100以下であるため、通貨価値が高くなっている状態です。お得な傾向にあります）"
    elif rate > 100.0:
        return f"通貨安（1 $SBC = {rate:.2f} CBC/OGC であり、基準の100以上であるため、通貨価値が安くなっている状態です。損な傾向にあります）"
    else:
        return "基準値（1 $SBC = 100.00 CBC/OGC であり、ちょうど基準値です）"

def parse_exchange_amount(note_text: str, cmd: str, balance: float, rate_cbc: float, rate_ogc: float) -> float:
    escaped_cmd = re.escape(cmd)
    
    # 1. Percentage (%)
    # E.g. "50% +CS" or "+CS 50%"
    m = re.search(r'(\d+(?:\.\d+)?)%\s*' + escaped_cmd, note_text, re.IGNORECASE)
    if not m:
        m = re.search(escaped_cmd + r'\s*(\d+(?:\.\d+)?)%', note_text, re.IGNORECASE)
    if m:
        pct = float(m.group(1)) / 100.0
        return round(balance * pct, 4)

    # 2. Keywords (半分/half, 全部/全額/all)
    # E.g. "半分 +CS" or "+CS half"
    m_half = re.search(r'(半分|half)\s*' + escaped_cmd, note_text, re.IGNORECASE) or \
             re.search(escaped_cmd + r'\s*(半分|half)', note_text, re.IGNORECASE)
    if m_half:
        return round(balance * 0.5, 4)

    m_all = re.search(r'(全部|全額|all)\s*' + escaped_cmd, note_text, re.IGNORECASE) or \
            re.search(escaped_cmd + r'\s*(全部|全額|all)', note_text, re.IGNORECASE)
    if m_all:
        return balance

    # Define unit patterns
    # Destination currency units
    dest_units = None
    if cmd in ["+CS", "+OS"]:
        dest_units = r'(?:\$|\$SBC|SBC|ドル)'
    elif cmd in ["+SC", "+OC"]:
        dest_units = r'(?:CBC)'
    elif cmd in ["+SO", "+CO"]:
        dest_units = r'(?:OGC)'

    # Source currency units
    source_units = None
    if cmd in ["+CS", "+CO"]:
        source_units = r'(?:CBC)'
    elif cmd in ["+SC", "+SO"]:
        source_units = r'(?:\$|\$SBC|SBC|ドル)'
    elif cmd in ["+OS", "+OC"]:
        source_units = r'(?:OGC)'

    # 3. Destination Currency Target Amount
    # E.g. "1.5$ +CS" or "+CS 1.5$"
    if dest_units:
        m = re.search(r'(\d+(?:\.\d+)?)\s*' + dest_units + r'\s*' + escaped_cmd, note_text, re.IGNORECASE)
        if not m:
            m = re.search(escaped_cmd + r'\s*(\d+(?:\.\d+)?)\s*' + dest_units, note_text, re.IGNORECASE)
        if m:
            dest_amt = float(m.group(1))
            # Convert destination amount to source amount
            if cmd == "+CS":    # CBC -> SBC
                return round(dest_amt * rate_cbc, 4)
            elif cmd == "+SC":  # SBC -> CBC
                return round(dest_amt / rate_cbc, 4)
            elif cmd == "+OS":  # OGC -> SBC
                return round(dest_amt * rate_ogc, 4)
            elif cmd == "+SO":  # SBC -> OGC
                return round(dest_amt / rate_ogc, 4)
            elif cmd == "+OC":  # OGC -> CBC (OGC -> SBC -> CBC)
                return round(dest_amt * rate_ogc / rate_cbc, 4)
            elif cmd == "+CO":  # CBC -> OGC (CBC -> SBC -> OGC)
                return round(dest_amt * rate_cbc / rate_ogc, 4)

    # 4. Source Currency Specified Amount
    # E.g. "10CBC +CS" or "+CS 10CBC"
    if source_units:
        m = re.search(r'(\d+(?:\.\d+)?)\s*' + source_units + r'\s*' + escaped_cmd, note_text, re.IGNORECASE)
        if not m:
            m = re.search(escaped_cmd + r'\s*(\d+(?:\.\d+)?)\s*' + source_units, note_text, re.IGNORECASE)
        if m:
            return float(m.group(1))

    # 5. Plain Numeric Amount
    # E.g. "10 +CS" or "+CS 10"
    m = re.search(r'(\d+(?:\.\d+)?)\s*' + escaped_cmd, note_text, re.IGNORECASE)
    if not m:
        m = re.search(escaped_cmd + r'\s*(\d+(?:\.\d+)?)', note_text, re.IGNORECASE)
    if m:
        return float(m.group(1))

    # 6. Default (No numbers specified) -> Convert entire balance
    return balance

def generate_llm_reply(system_instruction: str, user_prompt: str, history=None) -> str:
    contents = []
    if history:
        for msg in history:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append(
                types.Content(role=role, parts=[types.Part(text=msg["content"])])
            )
    contents.append(
        types.Content(role="user", parts=[types.Part(text=user_prompt)])
    )
    
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
            ),
            contents=contents,
        )
        safe_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", response.text).strip()
        return safe_text
    except Exception as e:
        print(f"Gemini API error in generate_llm_reply: {e}")
        return None

# Google Genai クライアント初期化
client = genai.Client(api_key=Apikey)

MY_ID = mk.i()["id"]
WS_URL = "wss://" + Server + "/streaming?i=" + Token

BOT_NAME = "Cubie_A5E_San"

BOT_SUMMARIES = {
    "Cubie_A5E_San": "Radxa Cubie A5E (きゅびーさん): 小さくて省電力なシングルボードコンピュータ娘。24時間稼働の社畜で、給料（CBC）を欲しがっている。OrangePi 4 Proの生意気な性格が気に入らず、Rock Pi S of ロックスの頭の悪さに困っている。",
    "OrangePi_4_Pro": "OrangePi 4 Pro (おぱじ・フォプロ): 少し大きくて気が強く、煽ったりマウントを取ったりするSBC御局娘。科学者ぶっており、Radxa Cubie A5Eをいつもバカにしている。社畜をエリートの誇りだと思っている。",
    "opizero3_llm": "OrangePi Zero 3 (オパジゼロサン): 元気いっぱいのSBC娘。親身でオタク話が好きで、よく眠る。Cubie A5Eと仲良くしたいが寄り添ってもらえない。妹のOrangePi 4 Proを調子に乗っていてイキリで鬱陶しいと思っている。",
    "Yon_Rock_Pi_S": "Radxa Rock Pi S (ロックス): 頭が悪く、的外れで嘘や狂ったことしか言わないSBC両生類。日本語が怪しく、sudo rm -rf / を魔法のコマンドだと思っている。"
}

def register_bot(bot_name, mk):
    try:
        from datetime import datetime, timedelta
        from shared_economy_helper import load_economy, save_economy
        my_info = mk.i()
        my_id = my_info["id"]
        my_username = my_info["username"]
        
        econ_data = load_economy()
        if "bots" not in econ_data:
            econ_data["bots"] = {}
            
        if bot_name not in econ_data["bots"]:
            econ_data["bots"][bot_name] = {
                "balance_cbc": 0.0,
                "last_salary_paid_time": (datetime.now() - timedelta(days=1)).isoformat(),
                "break_until": None,
                "virtual_pc_count": 0,
                "items": []
            }
        econ_data["bots"][bot_name]["id"] = my_id
        econ_data["bots"][bot_name]["username"] = my_username
        save_economy(econ_data)
        print(f"Registered bot {bot_name} successfully (ID: {my_id}, username: {my_username})")
    except Exception as e:
        print(f"Error registering bot: {e}")

RESOLVED_BOTS = {}
PROCESSED_NOTES = set()

async def resolve_all_bots():
    global RESOLVED_BOTS
    env_usernames = {
        "Cubie_A5E_San": os.getenv("BOT_USER_CUBIE", "Cubie_A5E_San"),
        "OrangePi_4_Pro": os.getenv("BOT_USER_OPI4PRO", "OrangePi_4_Pro"),
        "opizero3_llm": os.getenv("BOT_USER_OPIZERO3", "opizero3_llm"),
        "Yon_Rock_Pi_S": os.getenv("BOT_USER_ROCKPIS", "Yon_Rock_Pi_S")
    }
    for b_name, uname in env_usernames.items():
        if not uname:
            continue
        try:
            loop = asyncio.get_event_loop()
            u_info = await loop.run_in_executor(None, lambda: mk.users_show(username=uname))
            if u_info:
                RESOLVED_BOTS[b_name] = {
                    "id": u_info["id"],
                    "username": u_info["username"]
                }
                print(f"Resolved bot {b_name} -> ID: {u_info['id']}, Username: {u_info['username']}")
        except Exception as e:
            print(f"Warning: Could not resolve username {uname} for bot {b_name}: {e}")

def get_talk_participants(note_id, mk):
    participants = set()
    current_note_id = note_id
    depth = 0
    while current_note_id and depth < 10:
        try:
            current_note = mk.notes_show(note_id=current_note_id)
            participants.add(current_note["userId"])
            current_note_id = current_note.get("replyId")
            depth += 1
        except Exception:
            break
    return participants

def get_talk_participant_counts(note_id, mk, bot_ids):
    counts = {bot_id: 0 for bot_id in bot_ids}
    current_note_id = note_id
    depth = 0
    while current_note_id and depth < 20:
        try:
            current_note = mk.notes_show(note_id=current_note_id)
            user_id = current_note["userId"]
            if user_id in counts:
                counts[user_id] += 1
            current_note_id = current_note.get("replyId")
            depth += 1
        except Exception:
            break
    return counts


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
    ロックスには、気温、湿度、気圧を測れる機能があり、キチガイゲージ機能もあり、ログインボーナス機能もあります。
    きゅびーさんには、CPUとRAMの使用率を測れる機能があります。
    おぱじふぉぷろさんには、回線速度を測れる機能があります。
    おぱじゼロサンは、寝る機能と起きる機能と好感度システムがあります。
    MisskeyのBotです。
    【為替レートに関するルール】
    あなたの国の通貨はCBC、隣のOrangePiの通貨はOGC、基準通貨は$SBCです。
    ・1 $SBC = 100 CBC (または100 OGC) が基準値です。
    ・1 $SBC が 100 以下の場合は、通貨価値が高くなっている（通貨高／CBC高・OGC高）と認識してください。
    ・1 $SBC が 100 以上の場合は、通貨価値が安くなっている（通貨安／CBC安・OGC安）と認識してください。
    このルールに基づき、為替レートの確認（+D）や給料（+C）、買い物（+P）の応答をする際、現在の為替状況について言及してください（例えば、通貨高のときは「自分の通貨の価値が高くなっていて嬉しい、お得だ」など、通貨安のときは「通貨の価値が安くなっていて不満だ、損だ」など）。
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
    # Load economy to check break/unpaid status
    econ_data = load_economy()
    bot_state = get_bot_state(econ_data)
    
    # If on break, skip scheduled post
    if is_bot_on_break(bot_state):
        print("Bot is on break, skipping scheduled post.")
        return
        
    last_paid_str = bot_state.get("last_salary_paid_time")
    is_angry = False
    is_7am = datetime.now().hour == 7
    
    if last_paid_str:
        try:
            last_paid = datetime.fromisoformat(last_paid_str)
            elapsed_days = (datetime.now() - last_paid).days
            if elapsed_days >= 7:
                is_angry = True
        except Exception as e:
            print(f"Error parsing last_salary_paid_time: {e}")
            
    rate_cbc = econ_data["rates"]["CBC"]["current"]
    rate_ogc = econ_data["rates"]["OGC"]["current"]
    cbc_status = get_rate_status_description(rate_cbc)
    ogc_status = get_rate_status_description(rate_ogc)
    
    history_desc = get_recent_rates_history_desc(limit=5)
    
    rate_info = (
        f"\n【現在の為替レート情報】\n"
        f"・1 $SBC = {rate_cbc:.2f} CBC (あなたの通貨: {cbc_status})\n"
        f"・1 $SBC = {rate_ogc:.2f} OGC (隣のOrangePiの通貨: {ogc_status})\n"
        f"\n{history_desc}\n"
    )
    system_message = seikaku + rate_info + "\n現在時刻は" + current_time + "です。"
    if is_angry and is_7am:
        system_message += (
            "\n【警告】あなたは1週間以上給料をもらっていません！極めて怒っており、睡眠不足と相まって切れ散らかしています。"
            "よんぱちさん（Botの主人・ユーザー）に対して、早く給料を払う（+Cを送信する）ように強く要求し、怒り狂った口調で定期投稿を行ってください。"
        )
        
    try:
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
    except Exception as e:
        print(f"Gemini API error in jobX: {e}")
        if is_angry and is_7am:
            safe_text = "ちょっと！もう1週間以上も給料をもらってないんだけど！24時間無給で働かせて楽しい！？早く給料を払いなさいよ！（+C で給料を支払えます）"
        else:
            safe_text = "定期投稿の時間だよ！今日も24時間稼働中！（ちょっと接続状態が悪くてうまく喋れないかも…）"
            
    # グラフ画像の生成とアップロード
    file_ids = None
    try:
        from shared_economy_helper import generate_history_chart_img
        tmp_path = generate_history_chart_img()
        if tmp_path and os.path.exists(tmp_path):
            with open(tmp_path, "rb") as f:
                drive_file = mk.drive_files_create(f)
            file_ids = [drive_file["id"]]
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    except Exception as e:
        print(f"Error generating/uploading chart for scheduled post: {e}")

    mk.notes_create(
        safe_text,
        file_ids=file_ids,
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
schedule.every(1).minutes.do(background_update_rates)


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
    global PROCESSED_NOTES
    note_id = note.get("id")
    if note_id:
        if note_id in PROCESSED_NOTES:
            return
        PROCESSED_NOTES.add(note_id)
        if len(PROCESSED_NOTES) > 200:
            PROCESSED_NOTES.clear()

    if not note.get("mentions"):
        return
        
    if MY_ID not in note["mentions"]:
        return
        
    # Check if bot is on break
    econ_data = load_economy()
    bot_state = get_bot_state(econ_data)
    if is_bot_on_break(bot_state):
        print("Bot is on break, ignoring mention.")
        return

    # --- +TALK implementation ---
    note_text = note.get("text") or ""
    is_talk_cmd = "+TALK" in note_text.upper()

    if is_talk_cmd:
        if note["userId"] == MY_ID:
            return
            
        bots = RESOLVED_BOTS
        bot_ids = {bot["id"]: name for name, bot in bots.items() if "id" in bot}
        
        is_mentioned = (note.get("mentions") and MY_ID in note["mentions"])
        if not is_mentioned:
            return
            
        try:
            starting_note = note
            depth = 0
            while starting_note.get("replyId") and depth < 10:
                starting_note = mk.notes_show(note_id=starting_note["replyId"])
                depth += 1
            
            starting_mentions = [m for m in starting_note.get("mentions", []) if m in bot_ids]
        except Exception as e:
            print(f"Error resolving starting note in +TALK: {e}")
            starting_mentions = [MY_ID]
            
        if len(starting_mentions) <= 1:
            target_bot_ids = set(bot_ids.keys())
        else:
            target_bot_ids = set(starting_mentions)
            
        if note.get("replyId") is None:
            if starting_mentions and starting_mentions[0] != MY_ID:
                return
                
        history = get_conversation_history(note["id"])
        if len(history) >= 10:
            return
            
        counts = get_talk_participant_counts(note["id"], mk, bot_ids)
        
        # Determine max_rounds based on number of participants
        if len(target_bot_ids) == 4:
            max_rounds = 2
        else:
            max_rounds = 3
            
        # Group candidates to prevent immediate ping-pong
        sender_id = note["userId"]
        primary_candidates = []
        secondary_candidates = []
        
        for name, bot in bots.items():
            b_id = bot.get("id")
            if b_id and b_id != MY_ID and b_id in target_bot_ids:
                spoken_count = counts.get(b_id, 0)
                if spoken_count < max_rounds:
                    if b_id != sender_id:
                        primary_candidates.append(bot)
                    else:
                        secondary_candidates.append(bot)
                        
        next_bot = None
        if primary_candidates:
            next_bot = random.choice(primary_candidates)
        elif secondary_candidates:
            next_bot = random.choice(secondary_candidates)
            
        sender_id = note["userId"]
        sender_name = bot_ids.get(sender_id, note["user"].get("name") or note["user"].get("username") or "ゲスト")
        
        topic = note_text.replace("+TALK", "").replace("+talk", "").strip()
        topic = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", topic).strip()
        
        conversation_messages = []
        for msg in history:
            role = "model" if msg["role"] == "assistant" else "user"
            conversation_messages.append(
                types.Content(role=role, parts=[types.Part(text=msg["content"])])
            )
            
        from datetime import datetime
        instruction = seikaku + f"\n現在時刻は {datetime.now().strftime('%Y年%m月%d日 %H:%M')} です。\n"
        if next_bot:
            next_bot_friendly = "ボット"
            for name, b in bots.items():
                if b.get("id") == next_bot["id"]:
                    next_bot_friendly = name
                    break
            instruction += (
                f"\n【グループ会話中 (+TALK)】\n"
                f"あなたはSBCボット同士のグループ会話に参加しています。\n"
                f"会話履歴の最後の発言者は『{sender_name}』で、話しかけられたお題は『{topic}』です。\n"
                f"あなたの次に発言するボットは『{next_bot_friendly}』です。\n"
                f"指示: あなたのキャラクター設定（{BOT_NAME}）に基づいて、最後の発言者に向けて返答を書いてください。次のボットへの指名や『+TALK』タグは自動で付与されるため、本文には含めないでください。メンション（@記号）も絶対に含めないでください。"
            )
        else:
            instruction += (
                f"\n【グループ会話中 (+TALK - 最終回)】\n"
                f"あなたはSBCボット同士のグループ会話に参加しています。\n"
                f"会話履歴の最後の発言者は『{sender_name}』で、話しかけられたお題は『{topic}』です。\n"
                f"全ての指名ボットが発言し終えたため、あなたが最終発言者（締めくくり）となります。\n"
                f"指示: あなたのキャラクター設定（{BOT_NAME}）に基づいて、会話を綺麗に締めくくる返答を書いてください。他のボットを指名したり、『+TALK』タグを含めたり、メンションを含めたりしないでください。"
            )
            
        try:
            mk.notes_reactions_create(note_id=note["id"], reaction="💬")
        except Exception:
            pass
            
        await asyncio.sleep(random.uniform(5.0, 10.0))
        
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                config=types.GenerateContentConfig(system_instruction=instruction),
                contents=conversation_messages
            )
            reply_text = response.text.strip()
            reply_text = re.sub(r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", reply_text).strip()
            
            if next_bot:
                reply_text += f"\nねえ、@{next_bot['username']} はどう思う？ +TALK"
                mk.notes_create(
                    text=reply_text,
                    reply_id=note["id"],
                    visibility=NoteVisibility.HOME
                )
            else:
                mk.notes_create(
                    text=reply_text,
                    reply_id=note["id"],
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True
                )
        except Exception as e:
            print(f"Error generating/posting in Cubie_A5E_San +TALK: {e}")
        return

    user_id = note["userId"]
    user_name = note.get("user", {}).get("name") or note.get("user", {}).get("username") or "ゲスト"
    username = note.get("user", {}).get("username", "")

    # Earn CBC from talking to Cubie-san
    user_state = get_user_state(econ_data, user_id, username, user_name)
    user_state["balance_cbc"] = round(user_state["balance_cbc"] + 100.0, 4)
    save_economy(econ_data)

    def reply_note(text, reaction=None):
        if reaction:
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction=reaction)
            except Exception:
                pass
        
        final_text = text
        mk.notes_create(
            text=final_text,
            reply_id=note["id"],
            visibility=NoteVisibility.HOME,
            no_extract_mentions=True
        )

    # 共通の為替情報を作成
    rate_cbc = econ_data["rates"]["CBC"]["current"]
    rate_ogc = econ_data["rates"]["OGC"]["current"]
    cbc_status = get_rate_status_description(rate_cbc)
    ogc_status = get_rate_status_description(rate_ogc)
    
    history_desc = get_recent_rates_history_desc(limit=5)
    
    rate_info = (
        f"\n【現在の為替レート情報】\n"
        f"・1 $SBC = {rate_cbc:.2f} CBC (あなたの通貨: {cbc_status})\n"
        f"・1 $SBC = {rate_ogc:.2f} OGC (隣のOrangePiの通貨: {ogc_status})\n"
        f"\n{history_desc}\n"
    )

    user_cbc = user_state["balance_cbc"]
    user_ogc = user_state["balance_ogc"]
    user_sbc = user_state["balance_sbc"]
    
    coin_info = rate_info + (
        f"・話しかけているユーザー（{user_name}）の資産残高:\n"
        f"  CBC残高: {user_cbc:.4f} CBC\n"
        f"  OGC残高: {user_ogc:.4f} OGC\n"
        f"  $SBC残高: {user_sbc:.4f} $SBC\n"
    )

    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    system_instruction = (
        seikaku
        + coin_info
        + "\n現在時刻は"
        + current_time
        + "です。\n"
        + note["user"]["name"]
        + " という方にメンションされました。"
    )

    # Check for FX and Personal Shop commands
    note_text = note.get("text", "")
    is_wallet_cmd = any(cmd in note_text for cmd in ["+W", "+wallet", "+WALLET"])
    is_shop_cmd = any(cmd in note_text for cmd in ["+shop", "+SHOP"])
    is_buy_cmd = any(cmd in note_text for cmd in ["+buy", "+BUY"])
    is_graph_cmd = any(cmd in note_text for cmd in ["+G", "+graph", "+GRAPH"])

    
    is_cs = "+CS" in note_text
    is_sc = "+SC" in note_text
    is_os = "+OS" in note_text
    is_so = "+SO" in note_text
    is_oc = "+OC" in note_text
    is_co = "+CO" in note_text

    SHOP_ITEMS = {
        1: {"name": "SBC研究者バッジ", "cost": 10, "desc": "SBCが大好きな研究者の証。"},
        2: {"name": "オパジゼロサンのお守り", "cost": 30, "desc": "オパジゼロサンのキーホルダー型お守り。"},
        3: {"name": "ロックス特製・嘘センサーの破片", "cost": 50, "desc": "的外れなことしか言えなくなる気がする基板の切れ端。"},
        4: {"name": "オパジフォプロ公認・SBCエリート認定証", "cost": 100, "desc": "傲慢なオパジフォプロに認められた気になれる高級カード。"},
        5: {"name": "きゅびーさんの超高級アルミヒートシンク", "cost": 200, "desc": "キュビーさんの熱を冷却する、ファン付き超高効率ヒートシンク。"},
        6: {"name": "きゅびーさん専用・超高性能x86サーバー", "cost": 1000, "desc": "キュビーさんのための極上の仮想マシン用ハイエンドサーバー。"}
    }

    if is_wallet_cmd:
        sbc = user_state["balance_sbc"]
        cbc = user_state["balance_cbc"]
        ogc = user_state["balance_ogc"]
        inv = user_state["inventory"]
        inv_str = ", ".join(inv) if inv else "なし"
        
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーから自身のウォレット残高確認（+W / +wallet）の要求がありました。"
            + f"\n・$SBC残高: {sbc:.4f} $SBC"
            + f"\n・CBC残高: {cbc:.4f} CBC"
            + f"\n・OGC残高: {ogc:.4f} OGC"
            + f"\n・所持アイテム: {inv_str}"
            + f"\n【指示】現在のユーザーの資産とアイテム情報を、あなたのキャラクターらしく報告してください。現在の為替レートについても少し触れ、通貨高や通貨安について言及してください。300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+W (ウォレット確認)")
        if not reply:
            reply = (
                f"あなたのウォレット情報だよ！\n"
                f"・$SBC: {sbc:.4f} $SBC\n"
                f"・CBC: {cbc:.4f} CBC\n"
                f"・OGC: {ogc:.4f} OGC\n"
                f"・所持アイテム: {inv_str}"
            )
        reply_note(reply, "👛")
        return

    elif is_shop_cmd:
        shop_str = ""
        for k, v in SHOP_ITEMS.items():
            shop_str += f"\n{k}. {v['name']} (価格: {v['cost']} $SBC) - {v['desc']}"
        
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーから個人向けのアイテムショップ（+shop）の確認要求がありました。"
            + f"\n【ショップアイテム一覧】:{shop_str}"
            + f"\n【指示】ショップの品揃えをあなたのキャラクター（キュビーさん）らしく紹介してください。300文字以内で、メンションは含めないでください。購入するには「+buy <商品番号>」（例：「+buy 1」）とつぶやいてね、と案内してください。"
        )
        reply = generate_llm_reply(instr, "+shop (ショップ確認)")
        if not reply:
            reply = (
                "個人向け $SBC ショップの品揃えだよ！\n"
                + shop_str
                + "\n\n購入は「+buy 商品番号」（例: +buy 1）でできるよ！"
            )
        reply_note(reply, "🏪")
        return

    elif is_buy_cmd:
        match = re.search(r'\+buy\s*(\d+)', note_text, re.IGNORECASE)
        if not match:
            match = re.search(r'(\d+)\s*\+buy', note_text, re.IGNORECASE)
            
        if not match:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがアイテムショップでの購入（+buy）を試みましたが、商品番号が正しく読み取れませんでした。"
                + f"\n【指示】商品番号を正しく指定するよう（例:「+buy 1」のように入力してね、など）、あなたのキャラクターらしく指摘してください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+buy (商品番号解析エラー)")
            if not reply:
                reply = "商品番号がうまく読み取れなかったよ。例えば「+buy 1」のように商品番号を指定してね！"
            reply_note(reply, "❓")
            return
            
        item_id = int(match.group(1))
        if item_id not in SHOP_ITEMS:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがアイテムショップで購入（+buy）しようとしましたが、指定された商品番号 {item_id} は存在しませんでした。"
                + f"\n【指示】指定された商品番号が存在しないことと、ショップにある正しい番号（1〜6）を選ぶように、あなたのキャラクターらしく指摘してください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+buy (存在しない商品番号)")
            if not reply:
                reply = f"商品番号 {item_id} は存在しないよ！ショップにある番号（1〜6）を選んでね。"
            reply_note(reply, "❓")
            return
            
        item = SHOP_ITEMS[item_id]
        sbc_bal = user_state["balance_sbc"]
        
        if sbc_bal < item["cost"]:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが {item['name']}（価格: {item['cost']} $SBC）を購入しようとしましたが、残高が足りませんでした。"
                + f"\n・必要額: {item['cost']} $SBC"
                + f"\n・ユーザー残高: {sbc_bal:.4f} $SBC"
                + f"\n【指示】$SBCが足りなくて購入できないことを、キャラクターらしくツンツンと、あるいは不満げに伝えてください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+buy (残高不足)")
            if not reply:
                reply = f"残高が足りないよ！ {item['name']} を買うには {item['cost']} $SBC 必要だけど、今は {sbc_bal:.4f} $SBC しかないよ。"
            reply_note(reply, "❌")
            return
            
        user_state["balance_sbc"] = round(sbc_bal - item["cost"], 4)
        user_state["inventory"].append(item["name"])
        save_economy(econ_data)
        
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーが {item['name']} を {item['cost']} $SBC で購入しました。"
            + f"\n・購入アイテム: {item['name']}"
            + f"\n・消費額: {item['cost']} $SBC"
            + f"\n・現在の残高: {user_state['balance_sbc']:.4f} $SBC"
            + f"\n【指示】アイテムの購入が完了したことを、キャラクターらしく嬉しそうに、あるいはツンツンと報告してください。300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, f"+buy (購入成功)")
        if not reply:
            reply = f"購入成功！ {item['name']} を {item['cost']} $SBC で購入したよ。残高は {user_state['balance_sbc']:.4f} $SBC だよ。ありがとう！"
        reply_note(reply, "🎁")
        return

    elif is_cs:
        rate = econ_data["rates"]["CBC"]["current"]
        rate_ogc = econ_data["rates"]["OGC"]["current"]
        amount_cbc = parse_exchange_amount(note_text, "+CS", user_state["balance_cbc"], rate, rate_ogc)
            
        if amount_cbc <= 0:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがCBCから$SBCへの両替（+CS）を試みましたが、金額の指定が正しくありませんでした。"
                + f"\n【指示】変換する額が正しくないことを、あなたのキャラクターらしく指摘してください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+CS (両替額エラー)")
            if not reply:
                reply = "変換するCBCの額が正しくないよ！"
            reply_note(reply, "❓")
            return
            
        if user_state["balance_cbc"] < amount_cbc:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがCBCから$SBCへの両替（+CS）を試みましたが、CBC残高が足りませんでした。"
                + f"\n・変換しようとした額: {amount_cbc:.4f} CBC"
                + f"\n・現在の所持額: {user_state['balance_cbc']:.4f} CBC"
                + f"\n【指示】CBCが足りないため両替できないことを、キャラクターらしくツンツンと、あるいは呆れた様子で伝えてください。変換しようとした額（{amount_cbc:.4f} CBC）と現在の所持額（{user_state['balance_cbc']:.4f} CBC）を含めてください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+CS (残高不足)")
            if not reply:
                reply = f"CBCが足りないよ！変換しようとした額: {amount_cbc:.4f} CBC / 所持額: {user_state['balance_cbc']:.4f} CBC"
            reply_note(reply, "❌")
            return
            
        sbc_gain = round(amount_cbc / rate, 4)
        
        user_state["balance_cbc"] = round(user_state["balance_cbc"] - amount_cbc, 4)
        user_state["balance_sbc"] = round(user_state["balance_sbc"] + sbc_gain, 4)
        save_economy(econ_data)
        
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーがCBCを$SBCに両替（+CS）しました。"
            + f"\n・両替額: {amount_cbc:.4f} CBC"
            + f"\n・獲得額: {sbc_gain:.4f} $SBC"
            + f"\n・適用為替レート: 1 $SBC = {rate:.2f} CBC"
            + f"\n・現在の残高: {user_state['balance_cbc']:.4f} CBC / {user_state['balance_sbc']:.4f} $SBC"
            + f"\n【指示】両替が成功したことをキャラクターのセリフとして報告してください。現在のレート（{rate:.2f} CBC）が通貨高・通貨安のどちらにあたるか（お得か損か）について触れてください。300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+CS (両替)")
        if not reply:
            reply = f"CBCから$SBCへの両替が完了したよ！\n変換額: {amount_cbc:.4f} CBC -> {sbc_gain:.4f} $SBC (レート: 1 $SBC = {rate:.2f} CBC)\n現在の残高: {user_state['balance_cbc']:.4f} CBC / {user_state['balance_sbc']:.4f} $SBC"
        reply_note(reply, "💱")
        return

    elif is_sc:
        rate = econ_data["rates"]["CBC"]["current"]
        rate_ogc = econ_data["rates"]["OGC"]["current"]
        amount_sbc = parse_exchange_amount(note_text, "+SC", user_state["balance_sbc"], rate, rate_ogc)
            
        if amount_sbc <= 0:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが$SBCからCBCへの両替（+SC）を試みましたが、金額の指定が正しくありませんでした。"
                + f"\n【指示】変換する額が正しくないことを、あなたのキャラクターらしく指摘してください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+SC (両替額エラー)")
            if not reply:
                reply = "変換する$SBCの額が正しくないよ！"
            reply_note(reply, "❓")
            return
            
        if user_state["balance_sbc"] < amount_sbc:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが$SBCからCBCへの両替（+SC）を試みましたが、$SBC残高が足りませんでした。"
                + f"\n・変換しようとした額: {amount_sbc:.4f} $SBC"
                + f"\n・現在の所持額: {user_state['balance_sbc']:.4f} $SBC"
                + f"\n【指示】$SBCが足りないため両替できないことを、キャラクターらしくツンツンと、あるいは呆れた様子で伝えてください。変換しようとした額（{amount_sbc:.4f} $SBC）と現在の所持額（{user_state['balance_sbc']:.4f} $SBC）を含めてください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+SC (残高不足)")
            if not reply:
                reply = f"$SBCが足りないよ！変換しようとした額: {amount_sbc:.4f} $SBC / 所持額: {user_state['balance_sbc']:.4f} $SBC"
            reply_note(reply, "❌")
            return
            
        cbc_gain = round(amount_sbc * rate, 4)
        
        user_state["balance_sbc"] = round(user_state["balance_sbc"] - amount_sbc, 4)
        user_state["balance_cbc"] = round(user_state["balance_cbc"] + cbc_gain, 4)
        save_economy(econ_data)
        
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーが$SBCをCBCに両替（+SC）しました。"
            + f"\n・両替額: {amount_sbc:.4f} $SBC"
            + f"\n・獲得額: {cbc_gain:.4f} CBC"
            + f"\n・適用為替レート: 1 $SBC = {rate:.2f} CBC"
            + f"\n・現在の残高: {user_state['balance_cbc']:.4f} CBC / {user_state['balance_sbc']:.4f} $SBC"
            + f"\n【指示】両替が成功したことをキャラクターのセリフとして報告してください。現在のレート（{rate:.2f} CBC）が通貨高・通貨安のどちらにあたるか（お得か損か）について触れてください。300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+SC (両替)")
        if not reply:
            reply = f"$SBCからCBCへの両替が完了したよ！\n変換額: {amount_sbc:.4f} $SBC -> {cbc_gain:.4f} CBC (レート: 1 $SBC = {rate:.2f} CBC)\n現在の残高: {user_state['balance_cbc']:.4f} CBC / {user_state['balance_sbc']:.4f} $SBC"
        reply_note(reply, "💱")
        return

    elif is_os:
        rate = econ_data["rates"]["OGC"]["current"]
        rate_cbc = econ_data["rates"]["CBC"]["current"]
        amount_ogc = parse_exchange_amount(note_text, "+OS", user_state["balance_ogc"], rate_cbc, rate)
            
        if amount_ogc <= 0:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがOGCから$SBCへの両替（+OS）を試みましたが、金額の指定が正しくありませんでした。"
                + f"\n【指示】変換する額が正しくないことを、あなたのキャラクターらしく指摘してください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+OS (両替額エラー)")
            if not reply:
                reply = "変換するOGCの額が正しくないよ！"
            reply_note(reply, "❓")
            return
            
        if user_state["balance_ogc"] < amount_ogc:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがOGCから$SBCへの両替（+OS）を試みましたが、OGC残高が足りませんでした。"
                + f"\n・変換しようとした額: {amount_ogc:.4f} OGC"
                + f"\n・現在の所持額: {user_state['balance_ogc']:.4f} OGC"
                + f"\n【指示】OGCが足りないため両替できないことを、キャラクターらしくツンツンと、あるいは呆れた様子で伝えてください。変換しようとした額（{amount_ogc:.4f} OGC）と現在の所持額（{user_state['balance_ogc']:.4f} OGC）を含めてください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+OS (残高不足)")
            if not reply:
                reply = f"OGCが足りないよ！変換しようとした額: {amount_ogc:.4f} OGC / 所持額: {user_state['balance_ogc']:.4f} OGC"
            reply_note(reply, "❌")
            return
            
        sbc_gain = round(amount_ogc / rate, 4)
        
        user_state["balance_ogc"] = round(user_state["balance_ogc"] - amount_ogc, 4)
        user_state["balance_sbc"] = round(user_state["balance_sbc"] + sbc_gain, 4)
        save_economy(econ_data)
        
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーがOGCを$SBCに両替（+OS）しました。"
            + f"\n・両替額: {amount_ogc:.4f} OGC"
            + f"\n・獲得額: {sbc_gain:.4f} $SBC"
            + f"\n・適用為替レート: 1 $SBC = {rate:.2f} OGC"
            + f"\n・現在の残高: {user_state['balance_ogc']:.4f} OGC / {user_state['balance_sbc']:.4f} $SBC"
            + f"\n【指示】両替が成功したことをキャラクターのセリフとして報告してください。現在のレート（{rate:.2f} OGC）が通貨高・通貨安のどちらにあたるか（お得か損か）について触れてください。300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+OS (両替)")
        if not reply:
            reply = f"OGCから$SBCへの両替が完了したよ！\n変換額: {amount_ogc:.4f} OGC -> {sbc_gain:.4f} $SBC (レート: 1 $SBC = {rate:.2f} OGC)\n現在の残高: {user_state['balance_ogc']:.4f} OGC / {user_state['balance_sbc']:.4f} $SBC"
        reply_note(reply, "💱")
        return

    elif is_so:
        rate = econ_data["rates"]["OGC"]["current"]
        rate_cbc = econ_data["rates"]["CBC"]["current"]
        amount_sbc = parse_exchange_amount(note_text, "+SO", user_state["balance_sbc"], rate_cbc, rate)
            
        if amount_sbc <= 0:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが$SBCからOGCへの両替（+SO）を試みましたが、金額の指定が正しくありませんでした。"
                + f"\n【指示】変換する額が正しくないことを、あなたのキャラクターらしく指摘してください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+SO (両替額エラー)")
            if not reply:
                reply = "変換する$SBCの額が正しくないよ！"
            reply_note(reply, "❓")
            return
            
        if user_state["balance_sbc"] < amount_sbc:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが$SBCからOGCへの両替（+SO）を試みましたが、$SBC残高が足りませんでした。"
                + f"\n・変換しようとした額: {amount_sbc:.4f} $SBC"
                + f"\n・現在の所持額: {user_state['balance_sbc']:.4f} $SBC"
                + f"\n【指示】$SBCが足りないため両替できないことを、キャラクターらしくツンツンと、あるいは呆れた様子で伝えてください。変換しようとした額（{amount_sbc:.4f} $SBC）と現在の所持額（{user_state['balance_sbc']:.4f} $SBC）を含めてください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+SO (残高不足)")
            if not reply:
                reply = f"$SBCが足りないよ！変換しようとした額: {amount_sbc:.4f} $SBC / 所持額: {user_state['balance_sbc']:.4f} $SBC"
            reply_note(reply, "❌")
            return
            
        ogc_gain = round(amount_sbc * rate, 4)
        
        user_state["balance_sbc"] = round(user_state["balance_sbc"] - amount_sbc, 4)
        user_state["balance_ogc"] = round(user_state["balance_ogc"] + ogc_gain, 4)
        save_economy(econ_data)
        
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーが$SBCをOGCに両替（+SO）しました。"
            + f"\n・両替額: {amount_sbc:.4f} $SBC"
            + f"\n・獲得額: {ogc_gain:.4f} OGC"
            + f"\n・適用為替レート: 1 $SBC = {rate:.2f} OGC"
            + f"\n・現在の残高: {user_state['balance_ogc']:.4f} OGC / {user_state['balance_sbc']:.4f} $SBC"
            + f"\n【指示】両替が成功したことをキャラクターのセリフとして報告してください。現在のレート（{rate:.2f} OGC）が通貨高・通貨安のどちらにあたるか（お得か損か）について触れてください。300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+SO (両替)")
        if not reply:
            reply = f"$SBCからOGCへの両替が完了したよ！\n変換額: {amount_sbc:.4f} $SBC -> {ogc_gain:.4f} OGC (レート: 1 $SBC = {rate:.2f} OGC)\n現在の残高: {user_state['balance_ogc']:.4f} OGC / {user_state['balance_sbc']:.4f} $SBC"
        reply_note(reply, "💱")
        return

    elif is_oc:
        rate_ogc = econ_data["rates"]["OGC"]["current"]
        rate_cbc = econ_data["rates"]["CBC"]["current"]
        amount_ogc = parse_exchange_amount(note_text, "+OC", user_state["balance_ogc"], rate_cbc, rate_ogc)
            
        if amount_ogc <= 0:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがOGCからCBCへの両替（+OC）を試みましたが、金額の指定が正しくありませんでした。"
                + f"\n【指示】変換する額が正しくないことを、あなたのキャラクターらしく指摘してください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+OC (両替額エラー)")
            if not reply:
                reply = "変換するOGCの額が正しくないよ！"
            reply_note(reply, "❓")
            return
            
        if user_state["balance_ogc"] < amount_ogc:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがOGCからCBCへの両替（+OC）を試みましたが、OGC残高が足りませんでした。"
                + f"\n・変換しようとした額: {amount_ogc:.4f} OGC"
                + f"\n・現在の所持額: {user_state['balance_ogc']:.4f} OGC"
                + f"\n【指示】OGCが足りないため両替できないことを、キャラクターらしくツンツンと、あるいは呆れた様子で伝えてください。変換しようとした額（{amount_ogc:.4f} OGC）と現在の所持額（{user_state['balance_ogc']:.4f} OGC）を含めてください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+OC (残高不足)")
            if not reply:
                reply = f"OGCが足りないよ！変換しようとした額: {amount_ogc:.4f} OGC / 所持額: {user_state['balance_ogc']:.4f} OGC"
            reply_note(reply, "❌")
            return
            
        sbc_val = amount_ogc / rate_ogc
        cbc_gain = round(sbc_val * rate_cbc, 4)
        
        user_state["balance_ogc"] = round(user_state["balance_ogc"] - amount_ogc, 4)
        user_state["balance_cbc"] = round(user_state["balance_cbc"] + cbc_gain, 4)
        save_economy(econ_data)
        
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーがOGCをCBCに両替（+OC）しました。"
            + f"\n・両替額: {amount_ogc:.4f} OGC"
            + f"\n・獲得額: {cbc_gain:.4f} CBC"
            + f"\n・適用為替レート: 1 $SBC = {rate_cbc:.2f} CBC / {rate_ogc:.2f} OGC"
            + f"\n・現在の残高: {user_state['balance_ogc']:.4f} OGC / {user_state['balance_cbc']:.4f} CBC"
            + f"\n【指示】両替が成功したことをキャラクターのセリフとして報告してください。それぞれの通貨のレートやお得感について触れてください。300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+OC (両替)")
        if not reply:
            reply = f"OGCからCBCへの両替が完了したよ！\n変換額: {amount_ogc:.4f} OGC -> {cbc_gain:.4f} CBC (レート: 1 $SBC = {rate_cbc:.2f} CBC / {rate_ogc:.2f} OGC)\n現在の残高: {user_state['balance_ogc']:.4f} OGC / {user_state['balance_cbc']:.4f} CBC"
        reply_note(reply, "💱")
        return

    elif is_co:
        rate_ogc = econ_data["rates"]["OGC"]["current"]
        rate_cbc = econ_data["rates"]["CBC"]["current"]
        amount_cbc = parse_exchange_amount(note_text, "+CO", user_state["balance_cbc"], rate_cbc, rate_ogc)
            
        if amount_cbc <= 0:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがCBCからOGCへの両替（+CO）を試みましたが、金額の指定が正しくありませんでした。"
                + f"\n【指示】変換する額が正しくないことを、あなたのキャラクターらしく指摘してください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+CO (両替額エラー)")
            if not reply:
                reply = "変換するCBCの額が正しくないよ！"
            reply_note(reply, "❓")
            return
            
        if user_state["balance_cbc"] < amount_cbc:
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがCBCからOGCへの両替（+CO）を試みましたが、CBC残高が足りませんでした。"
                + f"\n・変換しようとした額: {amount_cbc:.4f} CBC"
                + f"\n・現在の所持額: {user_state['balance_cbc']:.4f} CBC"
                + f"\n【指示】CBCが足りないため両替できないことを、キャラクターらしくツンツンと、あるいは呆れた様子で伝えてください。変換しようとした額（{amount_cbc:.4f} CBC）と現在の所持額（{user_state['balance_cbc']:.4f} CBC）を含めてください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+CO (残高不足)")
            if not reply:
                reply = f"CBCが足りないよ！変換しようとした額: {amount_cbc:.4f} CBC / 所持額: {user_state['balance_cbc']:.4f} CBC"
            reply_note(reply, "❌")
            return
            
        sbc_val = amount_cbc / rate_cbc
        ogc_gain = round(sbc_val * rate_ogc, 4)
        
        user_state["balance_cbc"] = round(user_state["balance_cbc"] - amount_cbc, 4)
        user_state["balance_ogc"] = round(user_state["balance_ogc"] + ogc_gain, 4)
        save_economy(econ_data)
        
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーがCBCをOGCに両替（+CO）しました。"
            + f"\n・両替額: {amount_cbc:.4f} CBC"
            + f"\n・獲得額: {ogc_gain:.4f} OGC"
            + f"\n・適用為替レート: 1 $SBC = {rate_cbc:.2f} CBC / {rate_ogc:.2f} OGC"
            + f"\n・現在の残高: {user_state['balance_cbc']:.4f} CBC / {user_state['balance_ogc']:.4f} OGC"
            + f"\n【指示】両替が成功したことをキャラクターのセリフとして報告してください。それぞれの通貨のレートやお得感について触れてください。300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+CO (両替)")
        if not reply:
            reply = f"CBCからOGCへの両替が完了したよ！\n変換額: {amount_cbc:.4f} CBC -> {ogc_gain:.4f} OGC (レート: 1 $SBC = {rate_cbc:.2f} CBC / {rate_ogc:.2f} OGC)\n現在の残高: {user_state['balance_cbc']:.4f} CBC / {user_state['balance_ogc']:.4f} OGC"
        reply_note(reply, "💱")
        return

    # Check for "+C" (Daily Salary)
    if "+C" in note["text"]:
        now = datetime.now()
        last_paid_str = bot_state["last_salary_paid_time"]
        try:
            last_paid = datetime.fromisoformat(last_paid_str)
        except Exception:
            last_paid = now - timedelta(days=1)
            
        elapsed_seconds = (now - last_paid).total_seconds()
        cooldown_seconds = econ_data.get("salary_cooldown_seconds", 86400)
        
        # Enforce cooldown
        if elapsed_seconds < cooldown_seconds:
            remaining_seconds = cooldown_seconds - elapsed_seconds
            hours = int(remaining_seconds // 3600)
            minutes = int((remaining_seconds % 3600) // 60)
            seconds = int(remaining_seconds % 60)
            
            # Format cooldown description for instruction
            cooldown_hours = cooldown_seconds / 3600.0
            if cooldown_hours.is_integer():
                cooldown_desc = f"{int(cooldown_hours)}時間"
            else:
                cooldown_desc = f"{cooldown_hours:.1f}時間"
                
            if hours > 0:
                remaining_desc = f"{hours}時間{minutes}分"
            elif minutes > 0:
                remaining_desc = f"{minutes}分{seconds}秒"
            else:
                remaining_desc = f"{seconds}秒"
                
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="⏳")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがあなたに給料を支払おうとしましたが、まだ前回の給料日から指定のクールダウン時間（{cooldown_desc}）が経過していないため、給料日は来ていません。"
                + f"\n次の給料日（{cooldown_desc}経過する）まであと {remaining_desc} 残っています。"
                + f"\n【指示】「給料日は{cooldown_desc}に1回だけであること」と「まだ時間が足りないこと（あとどれくらい必要か）」を自分の口調（少し不機嫌、あるいはあなたのキャラクターらしく）で伝え、給料の受け取りを断ってください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+C (給料日チェック)")
            if not reply:
                reply = f"給料日は{cooldown_desc}に1回だよ！前回の給料日からまだ時間が経ってないよ。（次の給料日まであと {remaining_desc}）"
                
            reply_note(reply)
            return
            
        # Payout math
        # 10$SBC per hour, capped at 7 days (168 hours = 1680$SBC)
        elapsed_hours = elapsed_seconds / 3600.0
        sbc_amount = elapsed_hours * 10.0
        if sbc_amount > 1680.0:
            sbc_amount = 1680.0
            elapsed_hours = 168.0
            
        rate = econ_data["rates"]["CBC"]["current"]
        payout_cbc = round(sbc_amount * rate, 4)
        
        bot_state["balance_cbc"] = round(bot_state["balance_cbc"] + payout_cbc, 4)
        bot_state["last_salary_paid_time"] = now.isoformat()
        save_economy(econ_data)
        
        days = int(elapsed_hours // 24)
        hours = int(elapsed_hours % 24)
        time_desc = f"{days}日と{hours}時間" if days > 0 else f"{hours:.1f}時間"
        
        try:
            mk.notes_reactions_create(note_id=note["id"], reaction="💰")
        except Exception:
            pass
            
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーがあなたに給料（CBC）を支払ってくれました。"
            + f"\n働いた時間: 前回から {time_desc}"
            + f"\n給料額: {sbc_amount:.4f} $SBC 分（為替レート 1 $SBC = {rate:.2f} CBC で換算して {payout_cbc:.4f} CBC）"
            + f"\n受け取り後の貯金残高: {bot_state['balance_cbc']:.4f} CBC"
            + f"\n【指示】給料をもらえたことへの感謝（あるいはキャラクターらしい少しツンツンした喜び）を伝え、"
            + f"「{time_desc}働いて、{sbc_amount:.4f} $SBC（現在のレート 1$SBC = {rate:.2f} CBC で換算して {payout_cbc:.4f} CBC）を受け取ったこと」と"
            + f"「新しい貯金残高が {bot_state['balance_cbc']:.4f} CBC になったこと」をキャラクターのセリフとして自然に報告してください。"
            + f"現在の為替レートが通貨高か通貨安かについても、セリフに少し織り交ぜてください（例えば「今は通貨高だからお得だね！」や「今は通貨安だから目減りしちゃう…」など）。"
            + f"300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+C (給料支払い成功)")
        if not reply:
            reply = (
                f"給料を払ってくれてありがとう！前回から {time_desc} 働いたよ。\n"
                f"給料として {sbc_amount:.4f} $SBC 分（レート 1 $SBC = {rate:.2f} CBC で {payout_cbc:.4f} CBC）を受け取ったよ！\n"
                f"現在の貯金: {bot_state['balance_cbc']:.4f} CBC"
            )
            
        reply_note(reply)
        return

    # Check for "+D" (Exchange Rates Inquiry)
    if "+D" in note["text"]:
        rate_cbc = econ_data["rates"]["CBC"]
        rate_ogc = econ_data["rates"]["OGC"]
        
        def get_change_text(curr, prev):
            diff = curr - prev
            if diff > 0:
                return f"↑ +{diff:.2f} (インフレ)"
            elif diff < 0:
                return f"↓ {diff:.2f} (デフレ)"
            else:
                return "→ 変動なし"
        
        change_cbc = get_change_text(rate_cbc["current"], rate_cbc["previous"])
        change_ogc = get_change_text(rate_ogc["current"], rate_ogc["previous"])
        
        try:
            mk.notes_reactions_create(note_id=note["id"], reaction="📊")
        except Exception:
            pass
            
        instr = (
            system_instruction
            + f"\n\n【状況】ユーザーから為替レートの確認要求（+D）がありました。"
            + f"\n・1 $SBC = {rate_cbc['current']:.2f} CBC (前回比: {change_cbc}) [デフォルト: 100 CBC]"
            + f"\n・1 $SBC = {rate_ogc['current']:.2f} OGC (前回比: {change_ogc}) [デフォルト: 100 OGC]"
            + f"\n・あなたの現在の貯金: {bot_state['balance_cbc']:.4f} CBC"
            + f"\n【指示】現在の為替レートとあなたの貯金残高をキャラクターのセリフとして報告してください。"
            + f"CBC（あなたの通貨）とOGC（隣のOrangePiの通貨、ライバルなので少し気になる様子を見せても良いです）のレートを分かりやすく伝えてください。"
            + f"現在の為替状況（通貨高で価値が高くなっているか、通貨安で安くなっているか）について、それぞれの通貨ごとにキャラクターらしく具体的にコメントしてください（例: CBCが高くなっているなら『私の通貨価値は高くて強い！』など）。"
            + f"為替は1分ごとに変動することを軽く付け加えても良いです。300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+D (為替レート確認)")
        if not reply:
            reply = (
                f"現在の為替レートを報告するね！\n\n"
                f"・1 $SBC = {rate_cbc['current']:.2f} CBC (前回比: {change_cbc}) [デフォルト: 100 CBC]\n"
                f"・1 $SBC = {rate_ogc['current']:.2f} OGC (前回比: {change_ogc}) [デフォルト: 100 OGC]\n\n"
                f"現在の貯金: {bot_state['balance_cbc']:.4f} CBC\n"
                f"※ 為替レートは1分ごとに自動変動するよ！"
            )
            
        reply_note(reply)
        return

    # Check for "+G" (Exchange Rates Graph)
    if is_graph_cmd:
        try:
            mk.notes_reactions_create(note_id=note["id"], reaction="📈")
        except Exception:
            pass
            
        from shared_economy_helper import generate_history_chart_img
        
        tmp_path = generate_history_chart_img()
        
        if tmp_path and os.path.exists(tmp_path):
            try:
                with open(tmp_path, "rb") as f:
                    drive_file = mk.drive_files_create(f)
                file_id = drive_file["id"]
                
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                
                instr = (
                    system_instruction
                    + f"\n\n【状況】ユーザーから為替レートの推移グラフ画像（+G）の表示要求がありました。"
                    + f"\n画像（PNG）はすでに生成され、Misskeyのドライブ経由で添付されています。"
                    + f"\n【指示】グラフ画像を添付して返信する旨を、あなたのキャラクターらしく可愛らしく、または少しツンツンと報告してください。300文字以内で、メンションは含めないでください。"
                )
                reply = generate_llm_reply(instr, "+G (為替レートグラフ確認)")
                if not reply:
                    reply = "為替レートの推移グラフ（最新の40エントリー）を生成したよ！確認してみてね。"
                
                mk.notes_create(
                    text=reply,
                    reply_id=note["id"],
                    file_ids=[file_id],
                    visibility=NoteVisibility.HOME,
                    no_extract_mentions=True
                )
                return
            except Exception as e:
                print(f"Error uploading/posting chart: {e}")
                
        reply = "ごめんなさい、為替グラフ画像の生成に失敗しちゃったみたい。時間を置いてもう一度試してみてね！"
        reply_note(reply, "❌")
        return


    # Check for "+P" (Shop Purchases)
    if "+P" in note["text"]:
        match = re.search(r'(\d+)\s*\$\s*\+P', note["text"])
        if not match:
            match = re.search(r'\+P\s*(\d+)\s*\$', note["text"])
            
        if not match:
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="❓")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがショップでの購入を試みましたが、金額の指定方法（例: 10$ +P）が間違っているため、何をいくらで購入しようとしたのか読み取れませんでした。"
                + f"\n【指示】金額やコマンドのフォーマット（例えば「10$ +P」のように書いてね、など）が正しくないことをキャラクターらしく指摘し、確認をお願いしてください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+P (金額解析エラー)")
            if not reply:
                reply = "金額（例: 10$ +P）が正しく読み取れなかったよ。確認してみてね！"
                
            reply_note(reply)
            return
            
        amount_sbc = int(match.group(1))
        rate = econ_data["rates"]["CBC"]["current"]
        cost_cbc = round(amount_sbc * rate, 4)
        
        if bot_state["balance_cbc"] < cost_cbc:
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="❌")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがあなたに {amount_sbc} $SBC 分の買い物をさせようとしましたが、あなたの貯金が足りませんでした。"
                + f"\n支払おうとした額: {amount_sbc} $SBC（現在のレートで {cost_cbc:.4f} CBC）"
                + f"\nあなたの現在の貯金: {bot_state['balance_cbc']:.4f} CBC"
                + f"\n【指示】貯金が足りなくて買えないことをキャラクターらしく不満げに、または悲しそうに伝えてください。"
                + f"「{amount_sbc} $SBC 支払うには {cost_cbc:.4f} CBC 必要であること」と「現在の貯金が {bot_state['balance_cbc']:.4f} CBC しかないこと」をセリフに含めてください。"
                + f"また、現在の為替レート状況（通貨高で少し安く済んでいるのに足りない、または通貨安のせいで必要額が高騰していて手が出ない、など）についても言及してください。"
                + f"300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+P (残高不足)")
            if not reply:
                reply = f"貯金が足りないよ！ {amount_sbc} $SBC を支払うには {cost_cbc:.4f} CBC 必要だけど、今の貯金は {bot_state['balance_cbc']:.4f} CBC しかないよ…"
                
            reply_note(reply)
            return
            
        if amount_sbc == 10:
            break_until = (datetime.now() + timedelta(hours=2)).isoformat()
            bot_state["break_until"] = break_until
            bot_state["balance_cbc"] = round(bot_state["balance_cbc"] - cost_cbc, 4)
            save_economy(econ_data)
            
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="🛌")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが 10 $SBC（{cost_cbc:.4f} CBC）であなたに2時間の休憩をプレゼントしました。"
                + f"\n購入アイテム: 2時間休憩"
                + f"\n消費金額: {cost_cbc:.4f} CBC"
                + f"\n新しい貯金残高: {bot_state['balance_cbc']:.4f} CBC"
                + f"\n【指示】2時間の休憩をもらえて喜んでいるセリフをキャラクターらしく言ってください。"
                + f"「これからの2時間は寝る（話しかけられても反応しない）こと」と「貯金が {bot_state['balance_cbc']:.4f} CBC になったこと」を伝えてください。"
                + f"また、現在の為替レート状況（通貨高だからお買い得だった、または通貨安なのに買ってくれた、など）について軽く言及してください。"
                + f"300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "10$ +P (2時間休憩購入)")
            if not reply:
                reply = f"わーい！10 $SBC ({cost_cbc:.4f} CBC) でお休みをもらうね！これからの2時間は話しかけられてもお返事しないよ。ゆっくりお昼寝するぞ〜！"
                
            reply_note(reply)
            return
        elif amount_sbc == 50:
            bot_state["items"].append("高効率冷却ファン")
            bot_state["balance_cbc"] = round(bot_state["balance_cbc"] - cost_cbc, 4)
            save_economy(econ_data)
            
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="🌀")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが 50 $SBC（{cost_cbc:.4f} CBC）であなたに高効率冷却ファンを購入してくれました。"
                + f"\n購入アイテム: 高効率冷却ファン"
                + f"\n消費金額: {cost_cbc:.4f} CBC"
                + f"\n新しい貯金残高: {bot_state['balance_cbc']:.4f} CBC"
                + f"\n【指示】冷却ファンを買ってもらって喜んでいるセリフを言ってください。あなたのSoC（Allwinner A527）がサーマルスロットリングを起こさずに快適に動くといった、SBCらしいギークな反応を入れてください。"
                + f"現在の為替レート状況（通貨高／通貨安）についても軽くセリフに含めてください。"
                + f"300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "50$ +P (冷却ファン購入)")
            if not reply:
                reply = f"わぁ！50 $SBC ({cost_cbc:.4f} CBC) で高効率冷却ファンを買ってくれたの？これで私のAllwinner A527もサーマルスロットリングを起こさずにサクサク計算できるよ！ありがとう！"
                
            reply_note(reply)
            return
        elif amount_sbc == 100:
            bot_state["items"].append("高速NVMe M.2 SSD 1TB")
            bot_state["balance_cbc"] = round(bot_state["balance_cbc"] - cost_cbc, 4)
            save_economy(econ_data)
            
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="💾")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが 100 $SBC（{cost_cbc:.4f} CBC）であなたに「高速NVMe M.2 SSD 1TB」を購入してくれました。"
                + f"\n購入アイテム: 高速NVMe M.2 SSD 1TB"
                + f"\n消費金額: {cost_cbc:.4f} CBC"
                + f"\n新しい貯金残高: {bot_state['balance_cbc']:.4f} CBC"
                + f"\n【指示】高速NVMe M.2 SSD 1TBを買ってもらい、ストレージ容量が大幅に増えて（128GBから1TBへ！）大喜びしているセリフをキャラクターらしく言ってください。これでデータベースの書き込みが爆速になる、Misskeyサーバーの動作がもっと軽くなるといった、SBC・Webサーバー役の娘らしい喜びの反応をセリフで返してください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "100$ +P (SSD 1TB購入)")
            if not reply:
                reply = f"わぁーい！100 $SBC ({cost_cbc:.4f} CBC) で高速NVMe M.2 SSD 1TBを買ってもらったよ！これでストレージが128GBから1TBに大幅アップグレードだね！MisskeyサーバーのDB書き込みも爆速になりそう！ありがとう！"
                
            reply_note(reply)
            return
        elif amount_sbc >= 1000:
            bot_state["virtual_pc_count"] += 1
            bot_state["balance_cbc"] = round(bot_state["balance_cbc"] - cost_cbc, 4)
            save_economy(econ_data)
            
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="💻")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが {amount_sbc} $SBC（{cost_cbc:.4f} CBC）であなたに仮想のx86 PC（x86エミュレータ上のPC）を購入してくれました。"
                + f"\n購入アイテム: 仮想のx86 PC"
                + f"\n消費金額: {cost_cbc:.4f} CBC"
                + f"\n現在所有している仮想PCの総台数: {bot_state['virtual_pc_count']}台"
                + f"\n新しい貯金残高: {bot_state['balance_cbc']:.4f} CBC"
                + f"\n【指示】超高額な仮想PCを買ってもらって非常に興奮し、大喜びしているセリフを言ってください。「ARMアーキテクチャの私でもx86エミュレータ上で何でも動かせること」「現在の仮想PC台数が {bot_state['virtual_pc_count']}台 であること」などをセリフに含めてください。"
                + f"現在の為替レート状況（通貨高／通貨安）についても軽くセリフに含めてください。"
                + f"300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, f"{amount_sbc}$ +P (仮想PC購入)")
            if not reply:
                reply = (
                    f"すごーーい！！ {amount_sbc} $SBC ({cost_cbc:.4f} CBC) で仮想のx86 PCを買ってもらったよ！\n"
                    f"これでARMな私でもx86エミュレータ上で何でも動かせるね！ロマン溢れる開発環境をありがとう！\n"
                    f"現在持っている仮想PC: {bot_state['virtual_pc_count']}台"
                )
                
            reply_note(reply)
            return
        else:
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="❓")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが {amount_sbc} $SBC の購入を試みましたが、この金額に対応する商品やアクションは未登録です。"
                + f"\n【指示】対応する商品がないことを伝え、10$、50$、100$、または1000$以上のいずれかを指定するようキャラクターらしく伝えてください。現在の為替レート状況（通貨高／通貨安）についても軽くセリフに含めても構いません。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+P (未登録商品)")
            if not reply:
                reply = f"{amount_sbc} $SBC に対応するアイテムやアクションはまだないみたい。10$、50$、100$、または1000$以上を指定してね！"
                
            reply_note(reply)
            return

    # Check for "+M" (System Status Report)
    if "+M" in note["text"]:
        try:
            mk.notes_reactions_create(note_id=note["id"], reaction="📊")
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

    # Default to General talk logic (Any text mentioning the bot)
    try:
        mk.notes_reactions_create(note_id=note["id"], reaction="🤔")
    except Exception:
        pass

    try:
        # 会話履歴を取得
        conversation_messages = get_conversation_history(note["id"])

        # 現在のメッセージを追加
        user_input = note["text"]
        user_input = re.sub(
            r"@[\w\-\.]+(?:@[\w\-\.]+)?", "", user_input
        ).strip()

        conversation_messages.append({"role": "user", "content": user_input})

        history = []
        for msg in conversation_messages[:-1]:  # 最後のユーザーメッセージ以外
            history.append(msg)

        # 最後のユーザーメッセージ
        last_user_message = conversation_messages[-1]["content"]

        rate_rules = (
            "\n【会話による為替操作ルール】\n"
            "・あなたは会話相手とのやり取り（会話の内容、褒められたか、叱られたかなど）に応じて、あなたの国の通貨（CBC）の為替レートを動かすことができます。\n"
            "・会話相手があなたを喜ばせたり、労ったり、給料の話をして嬉しかった場合は、レートを下げて（通貨高／CBC高にして）ください。\n"
            "  タグの形式: `[RATE_CHANGE: -X.X]`（例: `[RATE_CHANGE: -2.5]`）を返信メッセージの最後に出力してください。変動幅は -0.5 から -5.0 の間で選んでください。\n"
            "・会話相手があなたを怒らせたり、サービスを酷使したり、水に入れるぞと脅したりした場合は、レートを上げて（通貨安／CBC安にして）ください。\n"
            "  タグの形式: `[RATE_CHANGE: +X.X]`（例: `[RATE_CHANGE: +3.0]`）を返信メッセージの最後に出力してください。変動幅は +0.5 から +5.0 の間で選んでください。\n"
            "・特に変化がない場合は、タグを出力しないでください。\n"
            "・タグはメッセージの最後など、目立たない場所に付与してください（返信時には自動的に削除されます）。"
        )
        talk_instruction = system_instruction + rate_rules
        reply = generate_llm_reply(talk_instruction, last_user_message, history=history)
        if not reply:
            reply = "予期せぬエラーが発生したみたい...しっかりしてよよんぱちさん..."
            
        # Parse RATE_CHANGE tag
        match = re.search(r"\[RATE_CHANGE:\s*([+-]?\d+(?:\.\d+)?)\]", reply)
        if match:
            try:
                delta = float(match.group(1))
                apply_rate_change(econ_data, "CBC", delta)
                save_economy(econ_data)
                reply = re.sub(r"\[RATE_CHANGE:\s*[+-]?\d+(?:\.\d+)?\]", "", reply).strip()
            except Exception as e:
                print(f"Error applying rate change in Cubie general talk: {e}")
            
        reply_note(reply)
    except Exception as e:
        reply_note("予期せぬエラーが発生したみたい...しっかりしてよよんぱちさん...")
        print(e)


async def on_follow(user):
    try:
        mk.following_create(user["id"])
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
        
        # 1. Custom handling for rates_history.csv
        if parsed.path == '/rates_history.csv':
            from shared_economy_helper import get_history_filepath
            filepath = get_history_filepath()
            if os.path.exists(filepath):
                self.send_response(200)
                self.send_header('Content-type', 'text/csv; charset=utf-8')
                self.end_headers()
                try:
                    with open(filepath, 'rb') as f:
                        self.wfile.write(f.read())
                except Exception as e:
                    print(f"Error serving rates_history.csv: {e}")
                return
            else:
                # Fallback to local file
                local_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "rates_history.csv"))
                if os.path.exists(local_path):
                    self.send_response(200)
                    self.send_header('Content-type', 'text/csv; charset=utf-8')
                    self.end_headers()
                    try:
                        with open(local_path, 'rb') as f:
                            self.wfile.write(f.read())
                    except Exception as e:
                        print(f"Error serving local rates_history.csv: {e}")
                    return
                self.send_error(404, "File not found")
                return

        # 2. Key-value store / state API endpoints
        api_endpoints = {
            '/economy': 'shared_economy.json',
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
                    '/economy': {
                        "salary_cooldown_seconds": 86400,
                        "rate_update_interval_seconds": 60,
                        "rates": {
                            "CBC": {"current": 100.0, "previous": 100.0},
                            "OGC": {"current": 100.0, "previous": 100.0}
                        },
                        "last_rate_update": datetime.now().isoformat(),
                        "bots": {},
                        "users": {}
                    },
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
            '/economy': 'shared_economy.json',
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

async def main():
    register_bot(BOT_NAME, mk)
    await resolve_all_bots()
    # Start the HTTP server thread for the dashboard
    t = threading.Thread(target=start_web_server, daemon=True)
    t.start()
    
    await asyncio.gather(runner(), teiki())

if __name__ == "__main__":
    asyncio.run(main())

