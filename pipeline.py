import os, glob, json, time, tempfile, shutil, re
import smtplib
from email.mime.text import MIMEText
import requests
import olefile, zlib, struct
import zipfile, xml.etree.ElementTree as ET
import pdfplumber
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from supabase import create_client
from anthropic import Anthropic

# ===== 키: GitHub Secrets에서 환경변수로 주입됨 =====
G2B_API_KEY = unquote(os.environ["G2B_API_KEY"])
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ===== 한국시간 (서버는 UTC라 보정 필수) =====
KST = timezone(timedelta(hours=9))
def now_kst():
    return datetime.now(KST).replace(tzinfo=None)

KEYWORDS = ["홍보", "SNS", "마케팅", "영상", "동영상", "유튜브"]

OUR_INDUSTRY_CODES = {"9902", "3230", "3244", "1469", "9999"}

COMPANY_PROFILE = {
    "상호": "주식회사 쇼앤프루프",
    "소재지": "경기도 남양주시",
    "보유_업종코드": ["9902", "3230", "3244", "1469", "9999"],
    "보유_업종명": "광고대행업(9902), 방송영상독립제작자(3230), 비디오물제작업(3244), 소프트웨어사업자-디지털콘텐츠개발서비스사업(1469), 기타자유업종(9999)",
    "직접생산확인": ["8213160301"],
    "해당부문_종사기간_년": 4,
    "PM_최대경력_년": 4,
    "공공기관_완료실적_건수": 0,
    "공공기관_완료실적_금액_원": 0,
    "공공기관_진행중_실적": "1건 43,000,000원 (2027.05 완료 예정, 현재 수행 중)",
    "민간_완료실적_건수": 11,
    "민간_완료실적_금액_원": 287000000,
    "실적_주의": "공공기관 완료실적 없음(1건은 수행 중, 2027.05 완료 예정). 공고가 공공기관 완료실적을 요구하면 탈락. 진행 중 실적도 인정하거나 실적 종류 제한이 없으면 민간 실적 포함하여 판정.",
    "4대보험_가입자수": 7,
    "자본금_원": 10000000,
    "신용평가등급": "B0",
    "인증": ["벤처기업", "여성기업", "소기업"],
    "가능분야": ["온라인 SNS 운영", "홍보/SNS 영상 제작", "언론 보도", "홍보용 이미지 제작 (브로셔·리플릿·카드뉴스 등)"],
}
DISQUALIFY_RULES = [
    "1. 요구 업종코드가 9902/3230/3244/1469/9999 중 어느 것도 아니면 탈락",
    "2. 직접생산확인 8213160301(동영상제작서비스)을 요구하는데 다른 코드만 인정하면 탈락",
    "3. 지역제한이 있고 그 범위에 '경기도 남양주'가 포함되지 않으면 탈락",
    "4. PM(책임자) 경력 5년 이상을 요구하면 탈락 (우리 최대 4년)",
    "5. 공공기관 완료실적을 요구하면 탈락 (완료된 공공 실적 없음). 진행 중 실적도 인정하거나 실적 종류 제한이 없으면 민간 실적 2.87억으로 판정",
    "6. 입찰 참가단계에서 입찰보증금 현금 납부가 필수이면 탈락",
]

def clean_text(text):
    cleaned = []
    for line in text.split("\n"):
        for token in line.split():
            good = re.findall(r'[가-힣a-zA-Z0-9]', token)
            if len(token) and len(good) / len(token) >= 0.5:
                cleaned.append(token)
        cleaned.append("\n")
    result = " ".join(cleaned)
    result = re.sub(r'\s+\n\s+', '\n', result)
    result = re.sub(r'[ ]{2,}', ' ', result)
    return result.strip()

def _parse_section(data):
    out, i = [], 0
    while i < len(data):
        header = struct.unpack_from("<I", data, i)[0]
        tag = header & 0x3ff
        length = (header >> 20) & 0xfff
        i += 4
        if tag == 67:
            t = data[i:i+length].decode("utf-16", errors="ignore")
            t = "".join(c for c in t if c.isprintable() or c in "\n\t ")
            out.append(t)
        i += length
    return " ".join(out)

def extract_hwp_text(path):
    f = olefile.OleFileIO(path)
    header = f.openstream("FileHeader").read()
    is_compressed = (header[36] & 1) == 1
    sections = sorted([d for d in f.listdir() if d[0] == "BodyText"],
                      key=lambda x: int(x[1].replace("Section", "")))
    texts = []
    for sec in sections:
        data = f.openstream(sec).read()
        if is_compressed:
            data = zlib.decompress(data, -15)
        texts.append(_parse_section(data))
    f.close()
    return clean_text("\n".join(texts))

def extract_hwpx_text(path):
    texts = []
    with zipfile.ZipFile(path) as z:
        for name in sorted([n for n in z.namelist()
                            if n.startswith("Contents/section") and n.endswith(".xml")]):
            root = ET.fromstring(z.read(name))
            for t in root.iter():
                if (t.tag.endswith("}t") or t.tag == "t") and t.text:
                    texts.append(t.text)
    return clean_text(" ".join(texts))

def extract_pdf_text(path):
    texts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
    return clean_text("\n".join(texts))

def extract_any(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".hwp":  return extract_hwp_text(path)
        if ext == ".hwpx": return extract_hwpx_text(path)
        if ext == ".pdf":  return extract_pdf_text(path)
    except Exception as e:
        print(f"    추출 실패({ext}): {e}")
    return ""

def download_file(url, save_path, tries=2):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": "https://www.g2b.go.kr/",
    }
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 200 and len(r.content) > 0:
                with open(save_path, "wb") as fp:
                    fp.write(r.content)
                return True
        except Exception as e:
            print(f"    시도{attempt} 실패: {type(e).__name__}")
        time.sleep(1)
    return False

def check_industry_codes(text):
    found = set()
    found |= set(re.findall(r'업종\s*코드[^\d]{0,15}(\d{4})', text))
    found |= set(re.findall(r'업종\s*\(?\s*(\d{4})\s*\)?', text))
    found |= set(re.findall(r'\((\d{4})\)\s*(?:으로|로)\s*입찰\s*참가', text))
    if not found:
        return None, set()
    if found & OUR_INDUSTRY_CODES:
        return "통과", found
    return "탈락", found

def prioritize_text(text, limit=20000):
    keys = ["참가자격", "입찰참가", "업종", "면허", "등록", "제한", "직접생산", "자격요건", "실적"]
    lines = text.split("\n")
    prio = [ln for ln in lines if any(k in ln for k in keys)]
    head = "\n".join(prio)[:6000]
    return ("[자격 관련 발췌]\n" + head + "\n\n[전문]\n" + text)[:limit]

def analyze_notice(combined_text):
    prompt = f"""당신은 입찰공고 검토 전문가입니다. 아래 회사가 이 공고에 제안서를 낼 만한지 판단하세요.

[회사 프로필]
{json.dumps(COMPANY_PROFILE, ensure_ascii=False, indent=2)}

[탈락 조건] - 하나라도 명확히 해당되면 자격 부적합입니다.
{chr(10).join(DISQUALIFY_RULES)}

[공고 문서 내용]
{prioritize_text(combined_text)}

지시:
- 회사는 위에 나열된 업종코드(9902/3230/3244/1469/9999) 외에는 보유하지 않는다. 공고가 그 외 코드를 요구하면 반드시 탈락으로 판정할 것.
- 실적 요건은 공공/민간 구분을 반드시 확인할 것. 공공기관 완료실적을 요구하면 우리는 해당 실적이 없으므로 탈락.
- 자격 요건을 관대하게 해석하지 말 것. 요건이 명시되었는데 우리 보유 여부가 확실하지 않으면 통과가 아니라 판단불가로 표시할 것.
- 참가자격·등록업종·면허·실적 관련 문장은 빠짐없이 검토할 것.
- fit_score는 자격 탈락 여부와 무관하게, 순수하게 우리 가능분야와 과업이 맞는 정도만 0~100.
- evidence(원문 인용)에는 큰따옴표를 쓰지 말고 한 줄로.
- 아래 JSON만 출력. 설명·마크다운 금지.

{{"task_summary": {{"overview": "한 문장", "tasks": ["과업1"]}}, "disqualify_check": [{{"rule": "조건요약", "result": "통과/탈락/판단불가", "evidence": "원문 인용"}}], "fit_score": 정수, "fit_summary": "2~3문장", "risks": ["리스크1"], "recommendation": "참여 권장/검토 필요/참여 비권장"}}"""
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt},
                  {"role": "assistant", "content": "{"}],
    )
    text = "{" + resp.content[0].text
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)

def to_dt(raw):
    if not raw or not raw.strip(): return None
    try: return datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S").isoformat()
    except ValueError: return None

def to_num(raw):
    try: return float(str(raw).replace(",", "")) if raw not in (None, "") else None
    except ValueError: return None

def collect_files(item):
    files, seen = [], set()
    for i in range(1, 11):
        name = (item.get(f"ntceSpecFileNm{i}") or "").strip()
        url = (item.get(f"ntceSpecDocUrl{i}") or "").strip()
        if not (name and url):
            continue
        base = os.path.splitext(name)[0]
        if base in seen:
            continue
        seen.add(base)
        files.append({"name": name, "url": url})
    return files

def send_email(recommend_list):
    if not recommend_list:
        print("참여 권장 신규 공고 없음 → 메일 안 보냄")
        return
    gmail = os.environ["GMAIL_ADDRESS"]
    app_pw = os.environ["GMAIL_APP_PW"]
    받는사람 = [
        "sm.hwang@showandproof.com",
        "chang.son@showandproof.com",
        "kwanho.park@showandproof.com",
    ]
    대시보드주소 = "https://g2b-bid-dashboard-kfksyjfhu33x3iqhjptyuw.streamlit.app/"

    lines = ["오늘 새로 추천된 참여 권장 공고입니다.\n"]
    for r in recommend_list:
        mark = " ⚠️자격확인필요" if r.get("needs_check") else ""
        lines.append(f"■ {r['title']}{mark}")
        lines.append(f"   발주기관: {r['agency']} | 적합도: {r['score']}점 | 마감: {r['deadline']}")
        lines.append("")
    lines.append(f"\n자세히 보기 → {대시보드주소}")
    body = "\n".join(lines)

    msg = MIMEText(body)
    msg["Subject"] = f"[입찰알림] 참여 권장 신규 공고 {len(recommend_list)}건"
    msg["From"] = gmail
    msg["To"] = ", ".join(받는사람)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail, app_pw)
        server.sendmail(gmail, 받는사람, msg.as_string())
    print(f"메일 발송 완료: {len(recommend_list)}건 → {len(받는사람)}명")

def main():
    URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc"
    end = now_kst()
    start = end - timedelta(days=2)
    print(f"조회 기준시각(KST): {end:%Y-%m-%d %H:%M}")

    base = {
        "serviceKey": G2B_API_KEY, "inqryDiv": 1, "numOfRows": 100, "type": "json",
        "inqryBgnDt": start.strftime("%Y%m%d") + "0000",
        "inqryEndDt": end.strftime("%Y%m%d") + "2359",
    }
    all_items, page = [], 1
    while True:
        r = None
        for attempt in range(3):
            try:
                r = requests.get(URL, params={**base, "pageNo": page}, timeout=30)
                break
            except requests.exceptions.ConnectionError:
                print(f"  연결 실패 {attempt+1}/3, 5초 후 재시도")
                time.sleep(5)
        if r is None:
            print("네트워크 오류로 수집 중단")
            return
        body = r.json()["response"]["body"]
        total = body.get("totalCount", 0)
        items = body.get("items", [])
        if isinstance(items, dict): items = items.get("item", [])
        if not items: break
        all_items.extend(items)
        if len(all_items) >= total: break
        page += 1
        time.sleep(0.3)
    print(f"최근 2일 용역 공고 {len(all_items)}건 수집")

    now = now_kst()
    def keep(it):
        title = (it.get("bidNtceNm") or "").replace(" ", "")
        if not any(kw.replace(" ", "") in title for kw in KEYWORDS):
            return False
        raw = it.get("bidClseDt", "")
        if not raw: return False
        try: return datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S") >= now
        except ValueError: return False
    matched = [it for it in all_items if keep(it)]
    print(f"키워드+마감 필터 통과 {len(matched)}건")

    for it in matched:
        supabase.table("notices").upsert({
            "bid_notice_no": it.get("bidNtceNo"),
            "bid_notice_ord": it.get("bidNtceOrd") or "00",
            "title": it.get("bidNtceNm"),
            "budget": to_num(it.get("asignBdgtAmt") or it.get("presmptPrce")),
            "deadline": to_dt(it.get("bidClseDt")),
            "agency": it.get("ntceInsttNm"),
            "demand_org": it.get("dminsttNm"),
            "biz_category": "용역",
            "posted_at": to_dt(it.get("bidNtceDt")),
            "source_url": it.get("bidNtceDtlUrl"),
        }, on_conflict="bid_notice_no,bid_notice_ord").execute()

    new_recommends = []
    fail_count = 0          # 파일 다운로드 실패 누적 (해외 IP 차단 감지용)

    for it in matched:
        bid_no = it.get("bidNtceNo")
        ord_no = it.get("bidNtceOrd") or "00"

        exist = (supabase.table("notices").select("analysis")
                 .eq("bid_notice_no", bid_no)
                 .eq("bid_notice_ord", ord_no).execute())
        if exist.data and exist.data[0].get("analysis"):
            continue

        tmp = tempfile.mkdtemp()
        combined = ""
        try:
            for f in collect_files(it):
                ext = os.path.splitext(f["name"])[1].lower()
                if ext not in (".hwp", ".hwpx", ".pdf"): continue
                path = os.path.join(tmp, f["name"])
                if download_file(f["url"], path):
                    combined += extract_any(path) + "\n"
                else:
                    fail_count += 1
                    print(f"  파일 실패 {f['name']}")

            # 초반부터 계속 실패하면 g2b 접속 차단으로 보고 조기 중단
            if fail_count >= 8 and not combined.strip():
                print("\n※ 파일 다운로드가 연속 실패했습니다 (g2b 접속 차단 추정). 실행을 중단합니다.")
                return

            if not combined.strip():
                print(f"  [문서없음] {bid_no}")
                continue
            try:
                result = analyze_notice(combined)
            except Exception as e:
                print(f"  [판단실패] {bid_no}: {e}")
                continue

            disq = result.get("disqualify_check", [])

            ind_result, ind_found = check_industry_codes(combined)
            if ind_result == "탈락":
                disq.append({
                    "rule": "업종코드 코드검증 (자동)",
                    "result": "탈락",
                    "evidence": f"공고 요구 {sorted(ind_found)} / 보유 {sorted(OUR_INDUSTRY_CODES)} - 일치 없음",
                })
                print(f"    ※ 업종코드 자동탈락: 요구 {sorted(ind_found)}")

            has_unknown = any(d.get("result") == "판단불가" for d in disq)
            is_disq = any(d.get("result") == "탈락" for d in disq)
            status = "부적합 제외" if is_disq else "검토 대기"

            (supabase.table("notices").update({
                "quant_score": result.get("fit_score"),
                "task_summary": result.get("task_summary"),
                "analysis": {
                    "fit_summary": result.get("fit_summary"),
                    "risks": result.get("risks"),
                    "recommendation": result.get("recommendation"),
                    "disqualify_check": disq,
                    "needs_check": has_unknown,
                },
                "status": status,
            }).eq("bid_notice_no", bid_no)
              .eq("bid_notice_ord", ord_no).execute())

            flag = " ⚠️확인필요" if has_unknown else ""
            print(f"  [완료] {bid_no}-{ord_no}: {result.get('fit_score')}점 / {status}{flag}")

            if result.get("recommendation") == "참여 권장" and not is_disq:
                new_recommends.append({
                    "title": it.get("bidNtceNm"),
                    "agency": it.get("ntceInsttNm"),
                    "score": result.get("fit_score"),
                    "deadline": (it.get("bidClseDt") or "")[:16],
                    "needs_check": has_unknown,
                })
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    send_email(new_recommends)
    print("파이프라인 완료")

if __name__ == "__main__":
    main()
