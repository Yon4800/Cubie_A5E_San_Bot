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

# Economy State File Path (default to current directory, or a URL)
ECONOMY_STATE_PATH = os.getenv("ECONOMY_STATE_PATH", "shared_economy.json")
ECONOMY_HTTP_HEADER_KEY = os.getenv("ECONOMY_HTTP_HEADER_KEY")
ECONOMY_HTTP_HEADER_VALUE = os.getenv("ECONOMY_HTTP_HEADER_VALUE")

def get_http_headers():
    headers = {"Content-Type": "application/json"}
    if ECONOMY_HTTP_HEADER_KEY and ECONOMY_HTTP_HEADER_VALUE:
        headers[ECONOMY_HTTP_HEADER_KEY] = ECONOMY_HTTP_HEADER_VALUE
    return headers

def get_economy_filepath():
    path = ECONOMY_STATE_PATH
    if path.startswith(("http://", "https://")):
        return path
    if not os.path.isabs(path):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.abspath(os.path.join(base_dir, path))
    return path

def update_exchange_rates(data, now):
    for coin in ["CBC", "OGC"]:
        # Ensure rates structure exists
        if "rates" not in data:
            data["rates"] = {}
        if coin not in data["rates"]:
            data["rates"][coin] = {"current": 100.0, "previous": 100.0}
        
        current = data["rates"][coin]["current"]
        
        # Determine fluctuation (absolute change)
        if random.random() < 0.20:  # 20% chance of sudden market shock
            change = random.uniform(0.5, 4.0)
        else:
            change = random.uniform(0.1, 0.5)
            
        if random.random() < 0.5:
            change = -change
            
        new_rate = current + change
        # Clamp to [10.0, 500.0] for wider movement
        new_rate = max(10.0, min(500.0, round(new_rate, 2)))
        
        data["rates"][coin]["previous"] = current
        data["rates"][coin]["current"] = new_rate
        
    data["last_rate_update"] = now.isoformat()

def check_and_update_rates_on_load(data):
    now = datetime.now()
    try:
        last_update = datetime.fromisoformat(data["last_rate_update"])
    except Exception:
        last_update = now - timedelta(days=1) # Force update if invalid
        
    interval = data.get("rate_update_interval_seconds", 60) # Default to 10 minutes (600 seconds)
    # If interval or more has passed
    if (now - last_update).total_seconds() >= interval:
        update_exchange_rates(data, now)
        return True
    return False

def load_economy():
    filepath = get_economy_filepath()
    now_str = datetime.now().isoformat()
    
    # Default state structure
    default_state = {
        "salary_cooldown_seconds": 86400,     # Default to 24 hours
        "rate_update_interval_seconds": 60,   # Default to 1 minute
        "rates": {
            "CBC": {"current": 100.0, "previous": 100.0},
            "OGC": {"current": 100.0, "previous": 100.0}
        },
        "last_rate_update": now_str,
        "bots": {}
    }
    
    data = default_state
    is_new = False
    
    if filepath.startswith(("http://", "https://")):
        try:
            res = requests.get(filepath, headers=get_http_headers(), timeout=5)
            if res.status_code == 200:
                loaded = res.json()
                if isinstance(loaded, dict):
                    # For services like JSONBin.io that wrap the data in a "record" key
                    if "record" in loaded:
                        data = loaded["record"]
                    else:
                        data = loaded
            else:
                is_new = True
        except Exception as e:
            print(f"Error loading remote economy state: {e}")
            is_new = True
    else:
        is_new = not os.path.exists(filepath)
        if not is_new:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
            except Exception as e:
                print(f"Error loading economy state: {e}")
                is_new = True
            
    # Ensure structure exists
    if "salary_cooldown_seconds" not in data:
        data["salary_cooldown_seconds"] = default_state["salary_cooldown_seconds"]
    if "rate_update_interval_seconds" not in data:
        data["rate_update_interval_seconds"] = default_state["rate_update_interval_seconds"]
        
    if "rates" not in data:
        data["rates"] = default_state["rates"]
    for coin in ["CBC", "OGC"]:
        if coin not in data["rates"]:
            data["rates"][coin] = {"current": 100.0, "previous": 100.0}
        elif not isinstance(data["rates"][coin], dict):
            data["rates"][coin] = {"current": float(data["rates"][coin]), "previous": float(data["rates"][coin])}
            
    if "last_rate_update" not in data:
        data["last_rate_update"] = now_str
    if "bots" not in data:
        data["bots"] = {}
        
    # Check if rate needs an update
    updated = check_and_update_rates_on_load(data)
    if updated or is_new:
        save_economy(data)
        
    return data

def save_economy(data):
    filepath = get_economy_filepath()
    if filepath.startswith(("http://", "https://")):
        try:
            res = requests.put(filepath, json=data, headers=get_http_headers(), timeout=5)
            if res.status_code not in (200, 201, 204):
                print(f"Failed to save remote economy state, status: {res.status_code}, response: {res.text}")
        except Exception as e:
            print(f"Error saving remote economy state: {e}")
    else:
        dir_name = os.path.dirname(filepath)
        if not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, encoding='utf-8') as tf:
                json.dump(data, tf, indent=2, ensure_ascii=False)
                temp_name = tf.name
            os.replace(temp_name, filepath)
        except Exception as e:
            print(f"Error saving economy state: {e}")

def get_bot_state(data, bot_name="Cubie_A5E_San"):
    if bot_name not in data["bots"]:
        data["bots"][bot_name] = {
            "balance_cbc": 0.0,
            "last_salary_paid_time": datetime.now().isoformat(),
            "break_until": None,
            "virtual_pc_count": 0,
            "items": []
        }
    bot_data = data["bots"][bot_name]
    # Ensure fields exist
    if "balance_cbc" not in bot_data:
        bot_data["balance_cbc"] = 0.0
    if "last_salary_paid_time" not in bot_data:
        bot_data["last_salary_paid_time"] = datetime.now().isoformat()
    if "break_until" not in bot_data:
        bot_data["break_until"] = None
    if "virtual_pc_count" not in bot_data:
        bot_data["virtual_pc_count"] = 0
    if "items" not in bot_data:
        bot_data["items"] = []
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
    
    rate_info = (
        f"\n【現在の為替レート情報】\n"
        f"・1 $SBC = {rate_cbc:.2f} CBC (あなたの通貨: {cbc_status})\n"
        f"・1 $SBC = {rate_ogc:.2f} OGC (隣のOrangePiの通貨: {ogc_status})\n"
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

    # 共通の為替情報を作成
    rate_cbc = econ_data["rates"]["CBC"]["current"]
    rate_ogc = econ_data["rates"]["OGC"]["current"]
    cbc_status = get_rate_status_description(rate_cbc)
    ogc_status = get_rate_status_description(rate_ogc)
    
    rate_info = (
        f"\n【現在の為替レート情報】\n"
        f"・1 $SBC = {rate_cbc:.2f} CBC (あなたの通貨: {cbc_status})\n"
        f"・1 $SBC = {rate_ogc:.2f} OGC (隣のOrangePiの通貨: {ogc_status})\n"
    )

    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    system_instruction = (
        seikaku
        + rate_info
        + "\n現在時刻は"
        + current_time
        + "です。\n"
        + note["user"]["name"]
        + " という方にメンションされました。"
    )

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
                
            mk.notes_create(
                text=reply,
                reply_id=note["id"],
                visibility=NoteVisibility.HOME,
                no_extract_mentions=True
            )
            return
            
        # Payout math
        # 10$SBC per hour, capped at 7 days (168 hours = 1680$SBC)
        elapsed_hours = elapsed_seconds / 3600.0
        sbc_amount = elapsed_hours * 10.0
        if sbc_amount > 1680.0:
            sbc_amount = 1680.0
            elapsed_hours = 168.0
            
        rate = econ_data["rates"]["CBC"]["current"]
        payout_cbc = round(sbc_amount * rate, 2)
        
        bot_state["balance_cbc"] = round(bot_state["balance_cbc"] + payout_cbc, 2)
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
            + f"\n給料額: {sbc_amount:.1f} $SBC 分（為替レート 1 $SBC = {rate:.2f} CBC で換算して {payout_cbc:.2f} CBC）"
            + f"\n受け取り後の貯金残高: {bot_state['balance_cbc']:.2f} CBC"
            + f"\n【指示】給料をもらえたことへの感謝（あるいはキャラクターらしい少しツンツンした喜び）を伝え、"
            + f"「{time_desc}働いて、{sbc_amount:.1f} $SBC（現在のレート 1$SBC = {rate:.2f} CBC で換算して {payout_cbc:.2f} CBC）を受け取ったこと」と"
            + f"「新しい貯金残高が {bot_state['balance_cbc']:.2f} CBC になったこと」をキャラクターのセリフとして自然に報告してください。"
            + f"現在の為替レートが通貨高か通貨安かについても、セリフに少し織り交ぜてください（例えば「今は通貨高だからお得だね！」や「今は通貨安だから目減りしちゃう…」など）。"
            + f"300文字以内で、メンションは含めないでください。"
        )
        reply = generate_llm_reply(instr, "+C (給料支払い成功)")
        if not reply:
            reply = (
                f"給料を払ってくれてありがとう！前回から {time_desc} 働いたよ。\n"
                f"給料として {sbc_amount:.1f} $SBC 分（レート 1 $SBC = {rate:.2f} CBC で {payout_cbc:.2f} CBC）を受け取ったよ！\n"
                f"現在の貯金: {bot_state['balance_cbc']:.2f} CBC"
            )
            
        mk.notes_create(
            text=reply,
            reply_id=note["id"],
            visibility=NoteVisibility.HOME,
            no_extract_mentions=True
        )
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
            + f"\n・あなたの現在の貯金: {bot_state['balance_cbc']:.2f} CBC"
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
                f"現在の貯金: {bot_state['balance_cbc']:.2f} CBC\n"
                f"※ 為替レートは1分ごとに自動変動するよ！"
            )
            
        mk.notes_create(
            text=reply,
            reply_id=note["id"],
            visibility=NoteVisibility.HOME,
            no_extract_mentions=True
        )
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
                
            mk.notes_create(
                text=reply,
                reply_id=note["id"],
                visibility=NoteVisibility.HOME,
                no_extract_mentions=True
            )
            return
            
        amount_sbc = int(match.group(1))
        rate = econ_data["rates"]["CBC"]["current"]
        cost_cbc = round(amount_sbc * rate, 2)
        
        if bot_state["balance_cbc"] < cost_cbc:
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="❌")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーがあなたに {amount_sbc} $SBC 分の買い物をさせようとしましたが、あなたの貯金が足りませんでした。"
                + f"\n支払おうとした額: {amount_sbc} $SBC（現在のレートで {cost_cbc:.2f} CBC）"
                + f"\nあなたの現在の貯金: {bot_state['balance_cbc']:.2f} CBC"
                + f"\n【指示】貯金が足りなくて買えないことをキャラクターらしく不満げに、または悲しそうに伝えてください。"
                + f"「{amount_sbc} $SBC 支払うには {cost_cbc:.2f} CBC 必要であること」と「現在の貯金が {bot_state['balance_cbc']:.2f} CBC しかないこと」をセリフに含めてください。"
                + f"また、現在の為替レート状況（通貨高で少し安く済んでいるのに足りない、または通貨安のせいで必要額が高騰していて手が出ない、など）についても言及してください。"
                + f"300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "+P (残高不足)")
            if not reply:
                reply = f"貯金が足りないよ！ {amount_sbc} $SBC を支払うには {cost_cbc:.2f} CBC 必要だけど、今の貯金は {bot_state['balance_cbc']:.2f} CBC しかないよ…"
                
            mk.notes_create(
                text=reply,
                reply_id=note["id"],
                visibility=NoteVisibility.HOME,
                no_extract_mentions=True
            )
            return
            
        if amount_sbc == 10:
            break_until = (datetime.now() + timedelta(hours=2)).isoformat()
            bot_state["break_until"] = break_until
            bot_state["balance_cbc"] = round(bot_state["balance_cbc"] - cost_cbc, 2)
            save_economy(econ_data)
            
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="🛌")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが 10 $SBC（{cost_cbc:.2f} CBC）であなたに2時間の休憩をプレゼントしました。"
                + f"\n購入アイテム: 2時間休憩"
                + f"\n消費金額: {cost_cbc:.2f} CBC"
                + f"\n新しい貯金残高: {bot_state['balance_cbc']:.2f} CBC"
                + f"\n【指示】2時間の休憩をもらえて喜んでいるセリフをキャラクターらしく言ってください。"
                + f"「これからの2時間は寝る（話しかけられても反応しない）こと」と「貯金が {bot_state['balance_cbc']:.2f} CBC になったこと」を伝えてください。"
                + f"また、現在の為替レート状況（通貨高だからお買い得だった、または通貨安なのに買ってくれた、など）について軽く言及してください。"
                + f"300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "10$ +P (2時間休憩購入)")
            if not reply:
                reply = f"わーい！10 $SBC ({cost_cbc:.2f} CBC) でお休みをもらうね！これからの2時間は話しかけられてもお返事しないよ。ゆっくりお昼寝するぞ〜！"
                
            mk.notes_create(
                text=reply,
                reply_id=note["id"],
                visibility=NoteVisibility.HOME,
                no_extract_mentions=True
            )
            return
        elif amount_sbc == 50:
            bot_state["items"].append("高効率冷却ファン")
            bot_state["balance_cbc"] = round(bot_state["balance_cbc"] - cost_cbc, 2)
            save_economy(econ_data)
            
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="🌀")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが 50 $SBC（{cost_cbc:.2f} CBC）であなたに高効率冷却ファンを購入してくれました。"
                + f"\n購入アイテム: 高効率冷却ファン"
                + f"\n消費金額: {cost_cbc:.2f} CBC"
                + f"\n新しい貯金残高: {bot_state['balance_cbc']:.2f} CBC"
                + f"\n【指示】冷却ファンを買ってもらって喜んでいるセリフを言ってください。あなたのSoC（Allwinner A527）がサーマルスロットリングを起こさずに快適に動くといった、SBCらしいギークな反応を入れてください。"
                + f"現在の為替レート状況（通貨高／通貨安）についても軽くセリフに含めてください。"
                + f"300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "50$ +P (冷却ファン購入)")
            if not reply:
                reply = f"わぁ！50 $SBC ({cost_cbc:.2f} CBC) で高効率冷却ファンを買ってくれたの？これで私のAllwinner A527もサーマルスロットリングを起こさずにサクサク計算できるよ！ありがとう！"
                
            mk.notes_create(
                text=reply,
                reply_id=note["id"],
                visibility=NoteVisibility.HOME,
                no_extract_mentions=True
            )
            return
        elif amount_sbc == 100:
            bot_state["items"].append("高速NVMe M.2 SSD 1TB")
            bot_state["balance_cbc"] = round(bot_state["balance_cbc"] - cost_cbc, 2)
            save_economy(econ_data)
            
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="💾")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが 100 $SBC（{cost_cbc:.2f} CBC）であなたに「高速NVMe M.2 SSD 1TB」を購入してくれました。"
                + f"\n購入アイテム: 高速NVMe M.2 SSD 1TB"
                + f"\n消費金額: {cost_cbc:.2f} CBC"
                + f"\n新しい貯金残高: {bot_state['balance_cbc']:.2f} CBC"
                + f"\n【指示】高速NVMe M.2 SSD 1TBを買ってもらい、ストレージ容量が大幅に増えて（128GBから1TBへ！）大喜びしているセリフをキャラクターらしく言ってください。これでデータベースの書き込みが爆速になる、Misskeyサーバーの動作がもっと軽くなるといった、SBC・Webサーバー役の娘らしい喜びの反応をセリフで返してください。300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, "100$ +P (SSD 1TB購入)")
            if not reply:
                reply = f"わぁーい！100 $SBC ({cost_cbc:.2f} CBC) で高速NVMe M.2 SSD 1TBを買ってもらったよ！これでストレージが128GBから1TBに大幅アップグレードだね！MisskeyサーバーのDB書き込みも爆速になりそう！ありがとう！"
                
            mk.notes_create(
                text=reply,
                reply_id=note["id"],
                visibility=NoteVisibility.HOME,
                no_extract_mentions=True
            )
            return
        elif amount_sbc >= 1000:
            bot_state["virtual_pc_count"] += 1
            bot_state["balance_cbc"] = round(bot_state["balance_cbc"] - cost_cbc, 2)
            save_economy(econ_data)
            
            try:
                mk.notes_reactions_create(note_id=note["id"], reaction="💻")
            except Exception:
                pass
                
            instr = (
                system_instruction
                + f"\n\n【状況】ユーザーが {amount_sbc} $SBC（{cost_cbc:.2f} CBC）であなたに仮想のx86 PC（x86エミュレータ上のPC）を購入してくれました。"
                + f"\n購入アイテム: 仮想のx86 PC"
                + f"\n消費金額: {cost_cbc:.2f} CBC"
                + f"\n現在所有している仮想PCの総台数: {bot_state['virtual_pc_count']}台"
                + f"\n新しい貯金残高: {bot_state['balance_cbc']:.2f} CBC"
                + f"\n【指示】超高額な仮想PCを買ってもらって非常に興奮し、大喜びしているセリフを言ってください。「ARMアーキテクチャの私でもx86エミュレータ上で何でも動かせること」「現在の仮想PC台数が {bot_state['virtual_pc_count']}台 であること」などをセリフに含めてください。"
                + f"現在の為替レート状況（通貨高／通貨安）についても軽くセリフに含めてください。"
                + f"300文字以内で、メンションは含めないでください。"
            )
            reply = generate_llm_reply(instr, f"{amount_sbc}$ +P (仮想PC購入)")
            if not reply:
                reply = (
                    f"すごーーい！！ {amount_sbc} $SBC ({cost_cbc:.2f} CBC) で仮想のx86 PCを買ってもらったよ！\n"
                    f"これでARMな私でもx86エミュレータ上で何でも動かせるね！ロマン溢れる開発環境をありがとう！\n"
                    f"現在持っている仮想PC: {bot_state['virtual_pc_count']}台"
                )
                
            mk.notes_create(
                text=reply,
                reply_id=note["id"],
                visibility=NoteVisibility.HOME,
                no_extract_mentions=True
            )
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
                
            mk.notes_create(
                text=reply,
                reply_id=note["id"],
                visibility=NoteVisibility.HOME,
                no_extract_mentions=True
            )
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
            
        mk.notes_create(
            text=reply,
            reply_id=note["id"],
            visibility=NoteVisibility.HOME,
            no_extract_mentions=True,
        )
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

        reply = generate_llm_reply(system_instruction, last_user_message, history=history)
        if not reply:
            reply = "予期せぬエラーが発生したみたい...しっかりしてよよんぱちさん..."
            
        mk.notes_create(
            text=reply,
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


if __name__ == "__main__":
    asyncio.run(main())
