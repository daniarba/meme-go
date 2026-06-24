# Solana Memecoin Sniper + Copy-Trade Bot

Pump.fun naye launches snipe karta hai + chosen wallets ko copy-trade karta hai.
Safety filter (RugCheck + GoPlus) se rugs/honeypots skip karta hai. Discord se control hota hai.

## ⚠️ Pehle yeh zaroor parho

- Bot DEFAULT mein `DRY_RUN=true` pe hai — koi real paisa move nahi hota, sirf simulate karta hai aur Discord pe dikhata hai kya hota.
- **Kam se kam 1-2 hafte DRY_RUN pe chalao** aur dekho signals kaise aa rahe hain, safety filter kaisa kaam kar raha hai. Phir hi `DRY_RUN=false` karo.
- Jab live karo, ek **alag dedicated wallet** use karo jisme sirf wo SOL ho jo lose karne ke liye ready ho. Main wallet ki private key kabhi bot mein na daalo.
- Memecoins 99% time zero ho jate hain. Yeh tool execution/automation fast karta hai — risk khatam nahi karta.

## Tumhe yeh cheezein chahiye (step by step)

### 1. Helius API Key (RPC + chain monitoring)
- https://helius.dev pe signup karo (free tier available)
- Dashboard se API key copy karo → `.env` mein `HELIUS_API_KEY=`

### 2. GoPlus API Key (optional, free tier bina key ke bhi chalti hai but rate limit kam hai)
- https://gopluslabs.io pe signup karke key le lo → `.env` mein `GOPLUS_API_KEY=`
- RugCheck.xyz ke liye koi key nahi chahiye, public API hai.

### 3. Solana Trading Wallet
- Naya Phantom wallet banao (sirf trading ke liye, alag se)
- Settings → Export Private Key → copy karo → `.env` mein `SOLANA_PRIVATE_KEY=`
- Thoda SOL daalo (jitna risk karna hai, jaise 0.5-1 SOL se start karo)

### 4. Discord Bot Setup
1. https://discord.com/developers/applications → **New Application**
2. Left menu → **Bot** → **Reset Token** → copy karo → `.env` mein `DISCORD_BOT_TOKEN=`
3. Bot tab mein **Message Content Intent** ON karo
4. **OAuth2 → URL Generator** → scopes: `bot` **aur** `applications.commands` (slash commands `/start`, `/menu` ke liye zaroori hai) → permissions: `Send Messages`, `Read Message History`, `Use Slash Commands` → generate URL → apne server mein bot invite karo
5. Discord app mein **Settings → Advanced → Developer Mode** ON karo
6. Apne server mein 2 channels banao: `#bot-control` aur `#bot-alerts`
7. Har channel pe right-click → **Copy Channel ID** → `.env` mein:
   - `DISCORD_CONTROL_CHANNEL_ID=` (jahan se commands doge)
   - `DISCORD_ALERTS_CHANNEL_ID=` (jahan trades/alerts aayenge)

### 5. (Optional, baad mein) Copy-trade target wallets
- GMGN.ai pe jaake "Smart Money" / leaderboard se profitable wallets ke addresses copy karo
- `.env` mein `COPY_TRADE_WALLETS=address1,address2,address3`

## Kya Install Karna Hai

### Local testing ke liye (optional, agar pehle apne PC pe try karna ho)
| Cheez | Kaise |
|---|---|
| **Python 3.11** | python.org se installer, ya `winget install Python.Python.3.11` (Windows) |
| **Git** | git-scm.com se installer (Railway pe push karne ke liye zaroori) |
| **Python packages** | `pip install -r requirements.txt --break-system-packages` (yeh discord.py, aiohttp, websockets, solders, base58, python-dotenv sab install kar dega — koi alag se install nahi karna) |

Bas itna hi. Koi Node.js, Rust, ya extra system package nahi chahiye — `solders` (Solana keys ke liye) ki prebuilt wheel PyPI se aa jaati hai.

### Railway pe deploy karne ke liye
| Cheez | Kahan se |
|---|---|
| **Railway account** | railway.app pe GitHub se signup (free trial credits milte hain) |
| **GitHub repo** | apna code GitHub pe push karna hoga (private repo bana lo) |
| **Railway CLI** (optional, GitHub ke bina direct deploy ke liye) | `npm install -g @railway/cli` |

## Local Run (test karne ke liye)

```bash
pip install -r requirements.txt --break-system-packages
cp .env.example .env
# .env mein apni keys fill karo
python -m src.main
```

## Railway pe Deploy Karna (jaisa rentahuman-bot kiya tha)

### Option A — GitHub se (recommended)
```bash
cd memecoin-bot
git init
git add .
git commit -m "initial commit"
# GitHub pe naya PRIVATE repo banao, phir:
git remote add origin https://github.com/daniarba/memecoin-bot.git
git branch -M main
git push -u origin main
```
1. railway.app → **New Project** → **Deploy from GitHub repo** → apna repo select karo
2. Railway khud `requirements.txt` aur `railway.json` detect kar lega (Nixpacks builder)
3. Service ki **Variables** tab mein jaake `.env` ki saari keys ek-ek karke add karo (HELIUS_API_KEY, SOLANA_PRIVATE_KEY, DISCORD_BOT_TOKEN, etc.) — `.env` file khud kabhi commit/upload mat karo
4. **Settings → Volumes → Add Volume**, mount path `/app/data` daalo — yeh zaroori hai warna har redeploy pe SQLite database (positions/trade history) delete ho jayega
5. Deploy ho jayega aur logs tab mein dekh sakte ho

### Option B — Railway CLI se (GitHub ke bina, direct)
```bash
npm install -g @railway/cli
railway login
cd memecoin-bot
railway init
railway up
```
Phir Variables aur Volume wahi Option A jaisa dashboard se add kar lo.

## Pehla Hafta — Paper Trading Plan

`.env` mein `DRY_RUN=true` already default hai, kuch change nahi karna. Bas:
1. Deploy kar do, Discord `#bot-alerts` channel dekhte raho
2. Roz `!status` aur `!positions` se check karo kitne signals aaye, safety filter kya skip kar raha hai
3. Hafte ke end pe SQLite `trades` table dekho (`!status` ka PnL number) — agar logic sahi lagta hai (rugs skip ho rahe hain, entries sensible hain) tabhi `DRY_RUN=false` karna, warna ek aur hafta paper trading extend karo

## Discord Control Panel (Telegram-style buttons)

Control channel mein `/start` ya `/menu` likho (slash command) — bot ek button panel bhej dega, exactly jaise Telegram "Coin Sniper" bots mein hota hai:

```
🤖 Memecoin Sniper Bot — Online
[⚙️ Settings]  [👛 Wallet]
[📊 Positions] [📋 Orders]
[💰 PnL]       [⏸️ Pause]
```

- Buttons click karne pe ephemeral reply aati hai (sirf tumhe dikhti hai, channel clutter nahi hota)
- **Pause/Resume** button click karne pe state turant toggle hoti hai aur button ka label/color khud badal jata hai
- Bot start hone pe yeh panel control channel mein khud-ba-khud bhi post ho jata hai

Old-style text commands (`!status`, `!pause`, etc. neeche table mein) bhi backward-compatible kaam karte hain agar tum prefer karo.

## Discord Commands (control channel mein)

| Command | Kaam |
|---|---|
| `/start` ya `/menu` | Button control panel kholo |
| `!status` | Today ka PnL summary |
| `!positions` | Saare open positions ki list |
| `!pause` | Naye trades rok do (open positions monitor hote rahenge) |
| `!resume` | Wapas se trading shuru |
| `!mode` | DRY RUN ya LIVE confirm karo |
| `!setbalance <amount>` | Paper-trading ka virtual balance khud set karo (jaise `!setbalance 5`) - redeploy ki zaroorat nahi |
| `!balance` | Current paper-trading balance dikhao |
| `!devcheck <wallet>` | Kisi bhi wallet ki dev-history manually check karo |
| `!debug` | Pata lagao bot kahan stuck hai — WS events aa rahe hain, kitne filter ho rahe hain |

## Architecture

```
chain_monitor.py     -> Helius WebSocket: Pump.fun + Raydium LaunchLab/CPMM + Moonshot + copy-trade wallets
        |
safety_filter.py     -> RugCheck + GoPlus se rug/honeypot score (0-100)
dev_wallet_tracker.py -> Deployer ki history check: pehle kitne tokens banaye, kitne rugged nikle
        |
risk_manager.py      -> Position size, daily loss limit, max positions, staircase trailing stop
        |
executor.py          -> Jupiter API se actual buy/sell (DRY_RUN respect karta hai)
        |
database.py           -> SQLite: positions, trade history, daily stats, dev-wallet cache
        |
discord_bot.py        -> Control commands + live alerts
        |
stats.py               -> In-memory counters, !debug command ke liye debug visibility
```

## Multi-Source Discovery (Pump.fun + Raydium + Moonshot)

Bot ab 4 sources se naye tokens detect karta hai (`.env` mein `ENABLE_*_WATCHER` se on/off):

| Source | Program ID | Kya hai |
|---|---|---|
| Pump.fun | `6EF8rrecthR5...` | Original bonding-curve launchpad |
| Raydium LaunchLab | `LanMV9sAd7w...` | Raydium ka apna pump.fun-jaisa bonding-curve launchpad |
| Raydium CPMM | `CPMMoo8L3F4N...` | Direct community pools + LaunchLab graduations (current default pool type) |
| Moonshot | `MoonCVVNZFS...` | Dexscreener ka launchpad |

**Note:** "Raydium AMM v4" (OpenBook-wala legacy version) abhi monitor nahi ho raha — Raydium ne 2025 mein naye pools ke liye CPMM ko default bana diya hai, AMM v4 sirf purane pools ke liye reh gaya hai. Agar phir bhi chahiye, `config.py` mein `RAYDIUM_AMM_V4_PROGRAM_ID` already defined hai, `chain_monitor.SOURCES` dict mein ek line add karke watcher start ho jayega.

Har source ka apna instruction-keyword hai jo "naya token create hua" detect karta hai (jaise Pump.fun ke liye `"Instruction: Create"`). Yeh keywords public docs/community examples se verified kiye gaye hain, lekin live mainnet pe directly test nahi ho sake (sandbox environment se Helius tak access nahi tha) — isliye `!debug` command zaroor check karo deploy karne ke baad.

## Troubleshooting: "Bot 2 ghante se koi trade nahi le raha"

**Update — yeh asli bug mil gaya tha (logs se confirmed) aur fix ho gaya hai:** Helius free-tier rate limit (HTTP 429) lag raha tha. 4 sources (Pump.fun + Raydium x2 + Moonshot) ek sath bohot saari `getTransaction` calls bhej rahe the bina kisi throttling ke, aur Helius reject kar raha tha. Upar se, har event ko **serially** process kiya ja raha tha (next WebSocket message tab tak nahi padhte the jab tak pehle wale ka 20-second safety-delay khatam na ho) — isliye signals backlog mein phas rahe the.

Dono fix ho gaye:
1. **Rate-limit handling**: Saari Helius RPC calls ab ek shared semaphore (`RPC_MAX_CONCURRENT`, default 4) se guarded hain, aur 429 milne pe automatic retry-with-backoff karti hain. Failure ka exact reason (status code + response body) ab logs mein dikhta hai — generic "tx fetch failed" nahi.
2. **Concurrent event processing**: Naye token events ab background tasks ke taur pe process hote hain, WebSocket read loop block nahi hota. Matlab events backlog mein nahi phasenge.
3. **Staggered startup**: 4 WS connections ab 3-second gap se start hoti hain (sab ek sath connect karne se bhi 429 aata tha).

Agar phir bhi `!debug` mein `tx_fetch_failed` zyada dikhe, Railway logs mein dekho — ab exact wajah likhi hogi (429 ho to `RPC_MAX_CONCURRENT` ko 2 kar do `.env` mein; kisi aur error ho to woh bhi text mein dikhega).

---

Agar discovery hi nahi chal rahi (events 0 hain), yeh steps follow karo. Discord control channel mein `!debug` likho, yeh dikhayega:

1. **`Source Events: 0` for sab sources** → Helius WebSocket connect hi nahi ho raha. Check karo: `HELIUS_API_KEY` sahi hai, Railway logs mein `"subscription active"` message dikh raha hai ya reconnect warnings aa rahi hain.
2. **Source Events high hain, Matched = 0** → WS messages aa rahe hain lekin instruction-keyword match nahi ho raha (humne jo guess kiya wo us source ke liye exactly sahi nahi hai). `.env` mein `DEBUG_LOG_UNMATCHED=true` karo, redeploy karo, Railway logs mein raw log lines dekho aur `chain_monitor.py` ke `SOURCES` dict mein keyword update karo.
3. **New tokens detected high hai, Evaluated bhi high, lekin Bought = 0** → Safety filter ya dev-wallet tracker sab kuch reject kar raha hai. `skipped_safety` aur `skipped_devwallet` counts dekho — agar bohot zyada hain, `MIN_SAFETY_SCORE` (safety_filter.py) ya `DEV_WALLET_MAX_RUG_RATIO` thoda relax karo.
4. **tx_fetch_failed ya mint_extract_failed high** → Ab Railway logs mein exact reason dikhega (rate-limit, timeout, ya RPC error) — upar wala fix isi ko address karta hai.



Jab bhi naya Pump.fun token detect hota hai, bot uske **deployer wallet** ki history check karta hai:

1. Wallet ki last `DEV_WALLET_HISTORY_LIMIT` (default 40) transactions nikalta hai
2. Unme se Pump.fun "Create" instructions filter karta hai — yeh deployer ke purane tokens hain
3. Har purane token ka current liquidity DexScreener se check karta hai
4. Agar liquidity `DEAD_TOKEN_LIQUIDITY_USD` (default $200) se kam hai aur token `DEAD_TOKEN_MIN_AGE_HOURS` (default 12h) se purana hai → us token ko "rugged" maana jata hai
5. `rugged_count / total_created` ratio nikalta hai. Agar yeh `DEV_WALLET_MAX_RUG_RATIO` (default 0.5 = 50%) se zyada ho → naya token **skip** ho jata hai, chahe safety score kuch bhi ho

Results 1 ghante (`DEV_WALLET_CACHE_TTL_SEC`) cache hote hain — same deployer dobara token banaye to dobara saari history nahi check hoti.

**Manual check:** Discord mein `!devcheck <wallet_address>` se kisi bhi wallet ki history kabhi bhi check kar sakte ho.

**Limitation:** Yeh approach last 40 transactions tak limited hai — agar deployer wallet bohot active hai (unrelated transactions bhi bohot zyada), to kuch purane tokens miss ho sakte hain. Behtar accuracy ke liye Helius ki Enhanced Transactions API use kar sakte hain (paid tier), lekin abhi ke liye yeh free RPC approach kaafi hai.

## Troubleshooting: "Sirf Skip messages aa rahe hain, ghanton se koi trade nahi"

**Yeh diagnose ho gaya hai** - do wajuhaat thi:

1. **Dev-wallet threshold bohot strict tha (0.5 = 50%)**: Pump.fun pe ~95% tokens naturally "dead" ho jate hain (interest khatam, scam zaroori nahi) - har active deployer ka purana history isi wajah se "risky" dikhta tha, chahe genuine scammer ho ya nahi. RugCheck ka apna "Creator history of rugged tokens" check bhi isi signal pe overlap karta hai - dono mil ke double-penalize kar rahe the. Default ab `0.85` (85%) hai aur kam se kam 3 purane tokens chahiye judge karne ke liye.
2. **`ENABLE_RAYDIUM_CPMM_WATCHER`** check karo `.env`/Railway Variables mein - default `false` hona chahiye (CPMM ki extraction unreliable hai). Agar tumhare Railway Variables mein yeh purana `true` set hai, `false` kar do.

Agar threshold relax karne ke baad bhi bohot kam trades aa rahe hain, `DEV_WALLET_MAX_RUG_RATIO` ko `.env` mein `0.9` ya `0.95` tak bhi le ja sakte ho — yeh ek precision/recall trade-off hai, tumhe apne risk-tolerance ke hisab se tune karna hai. `!debug` se `skipped_devwallet` vs `skipped_safety` vs `bought` ka ratio dekho.

## Feature: Khud Apna Paper-Trading Balance Set Karo

Discord control channel mein:
```
!setbalance 5
```
Yeh paper-trading ka virtual balance turant **5 SOL** kar dega — koi redeploy ya `.env` edit nahi chahiye. `!balance` se current value check kar sakte ho. Yeh sirf DRY_RUN mode mein kaam karta hai (jo default hai); LIVE mode mein position sizing real wallet balance se hoti hai.

## Feature: Advanced Staircase Trailing Stop

Premium plugins jo "trailing stop" bechte hain, wo yehi mathematical logic karte hain — humne khud likh liya:

`.env` mein `TRAILING_STAIRCASE=15:0,30:15,60:30,100:60,200:120` ka matlab:
- Profit **+15%** ho jaye → stop-loss lock ho jata hai breakeven pe (0% — na profit na loss)
- Profit **+30%** ho jaye → stop-loss lock ho jata hai **+15%** pe
- Profit **+60%** ho jaye → stop-loss lock **+30%** pe
- Profit **+100%** ho jaye → stop-loss lock **+60%** pe
- Profit **+200%** ho jaye → stop-loss lock **+120%** pe

Stop-loss **kabhi neeche nahi aata** — sirf upar shift hota hai jaise jaise naya high banta hai.

Iske sath ek **peak-trailing** layer bhi hai (`TRAILING_PCT_FROM_PEAK=20`): jab tak koi tier trigger nahi hua, normal hard stop-loss kaam karta hai. Ek baar koi tier trigger ho jaye, agar price apne all-time-high se 20% neeche aa jaye, exit ho jata hai — chahe staircase floor abhi door ho. Yeh bade upside (jaise +300%) ko bhi reasonably capture karta hai bina yeh wait kiye ke koi specific tier hit ho.

Tiers `.env` mein apni marzi se edit kar sakte ho — koi code change nahi chahiye.

## Known limitations (next steps jab tum ready ho)

1. **Token decimals**: `solana_rpc.py` mein price calculation abhi approximate hai (6 decimals assume karta hai). Production ke liye `getTokenSupply` se actual decimals fetch karna chahiye — agle iteration mein add karunga.
2. **Sentiment layer**: LunarCrush/Twitter sentiment abhi integrate nahi hai — jab chaho add kar sakte hain `safety_filter.py` jaisa hi ek `sentiment.py` module bana ke.
3. **GMGN Cooperation API**: abhi Jupiter hi primary execution hai. GMGN approval mil jaye to ek `gmgn_executor.py` add kar ke fallback/secondary route bana sakte hain.
4. **Single RPC connection**: agar Helius WebSocket disconnect ho (high traffic mein hota hai), bot 5 sec mein reconnect try karta hai, lekin production-grade reliability ke liye Geyser/gRPC (Helius/Triton) better hai — yeh phase 2 mein upgrade kar sakte hain.
5. Real testing (Jupiter/RugCheck/GoPlus live calls) tumhare apne machine pe honi chahiye — sandbox environment mein external crypto APIs tak network access nahi tha, isliye maine logic (database, risk manager, position sizing, stop-loss/take-profit) ko local tests se verify kiya hai, lekin live API responses tumhe pehli run pe dekhne honge.
