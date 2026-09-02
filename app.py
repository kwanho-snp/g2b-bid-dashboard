import streamlit as st
from supabase import create_client
from datetime import datetime, timedelta, timezone, date

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="입찰공고 검토 보드", layout="wide")

# ===== 회사 자격 정보 (파이프라인과 동일하게 유지할 것) =====
KEYWORDS = ["홍보", "SNS", "마케팅", "영상", "동영상", "유튜브"]
COMPANY_INFO = {
    "보유 업종코드": "9902 광고대행업 / 3230 방송영상독립제작자 / 3244 비디오물제작업 / "
                  "1469 소프트웨어사업자(디지털콘텐츠개발서비스) / 9999 기타자유업종",
    "직접생산확인": "8213160301 (동영상제작서비스)",
    "소재지": "경기도 남양주시",
    "PM 최대경력": "4년",
    "공공기관 완료실적": "0건 (1건 4,300만원 수행 중, 2027.05 완료 예정)",
    "민간 완료실적": "11건 / 2억 8,700만원",
    "신용평가등급": "B0",
    "인증": "벤처기업, 여성기업, 소기업",
}

STATUS_OPTIONS = ["검토 대기", "검토중", "참여 결정", "미참여", "부적합 제외"]

def today_kst():
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()

@st.cache_data(ttl=30)
def load_notices():
    res = supabase.table("notices").select("*").order("posted_at", desc=True).execute()
    return res.data

def load_attachments(notice_id):
    res = supabase.table("attachments").select("*").eq("notice_id", notice_id).execute()
    return res.data

def latest_only(rows):
    """정정공고: 같은 공고번호는 가장 마지막 차수만 남김"""
    best = {}
    for n in rows:
        key = n.get("bid_notice_no")
        try:
            ord_val = int(str(n.get("bid_notice_ord") or "0"))
        except ValueError:
            ord_val = 0
        if key not in best or ord_val > best[key][0]:
            best[key] = (ord_val, n)
    return [v[1] for v in best.values()]

notices = latest_only(load_notices())

# ===== 사이드바: 현재 검색 조건 & 회사 자격 =====
with st.sidebar:
    st.subheader("🔍 검색 키워드")
    st.write(" · ".join(KEYWORDS))
    st.caption("공고명에 위 단어가 하나라도 포함되면 수집 (용역 공고 대상)")

    st.subheader("🏢 우리 회사 자격")
    for k, v in COMPANY_INFO.items():
        st.markdown(f"**{k}**")
        st.caption(v)

# ============================================================
# 상세 화면
# ============================================================
params = st.query_params
if "id" in params:
    nid = params["id"]
    notice = next((n for n in notices if n["id"] == nid), None)
    if notice:
        if st.button("← 목록으로"):
            del st.query_params["id"]
            st.rerun()

        st.title(notice["title"])
        budget = notice.get("budget")
        budget_str = f"{int(budget):,}원" if budget else "-"
        st.markdown(
            f"**적합도** {notice.get('quant_score','-')}점  |  "
            f"**발주기관** {notice.get('agency','-')}  |  "
            f"**예산** {budget_str}  |  "
            f"**마감** {(notice.get('deadline') or '-')[:16]}"
        )
        if notice.get("source_url"):
            st.markdown(f"[🔗 나라장터에서 원문 보기]({notice['source_url']})")

        analysis = notice.get("analysis") or {}
        if analysis.get("needs_check"):
            st.warning("⚠️ 자격 조건 중 판단불가 항목이 있습니다. 원문을 직접 확인하세요.")
        st.info(f"**추천: {analysis.get('recommendation','-')}**")

        ts = notice.get("task_summary") or {}
        if ts:
            st.subheader("과업 요약")
            st.write(ts.get("overview", ""))
            for t in ts.get("tasks", []):
                if isinstance(t, dict):
                    st.write("- " + t.get("title", "") + ": " + t.get("detail", ""))
                else:
                    st.write("- " + str(t))

        st.subheader("적합성 판단")
        st.write(analysis.get("fit_summary", "-"))

        risks = analysis.get("risks", [])
        if risks:
            st.subheader("리스크")
            for r in risks:
                st.write("- " + str(r))

        disq = analysis.get("disqualify_check", [])
        if disq:
            st.subheader("자격 조건 판정")
            for d in disq:
                icon = {"통과": "✅", "탈락": "❌", "판단불가": "❓"}.get(d.get("result"), "•")
                st.write(f"{icon} **{d.get('rule')}** — {d.get('result')}")
                if d.get("evidence"):
                    st.caption(f"근거: {d.get('evidence')}")

        atts = load_attachments(nid)
        if atts:
            st.subheader("첨부파일")
            for a in atts:
                st.write(f"- [{a.get('kind')}] {a.get('file_name')}")

        st.subheader("상태 변경")
        cur = notice.get("status", "검토 대기")
        new = st.selectbox("상태", STATUS_OPTIONS,
                           index=STATUS_OPTIONS.index(cur) if cur in STATUS_OPTIONS else 0)
        if new != cur:
            supabase.table("notices").update({"status": new}).eq("id", nid).execute()
            st.success(f"상태를 '{new}'(으)로 변경했습니다.")
            st.cache_data.clear()
    st.stop()

# ============================================================
# 리스트 화면
# ============================================================
st.title("📋 입찰공고 검토 보드")

qp = st.query_params
FILTERS = ["전체", "검토 대상만", "부적합 제외", "참여 권장만"]
SORTS = ["게시일자", "입찰마감일", "검토점수", "사업예산"]

f = st.radio("필터", FILTERS, horizontal=True,
             index=FILTERS.index(qp.get("f")) if qp.get("f") in FILTERS else 0)
show_expired = st.checkbox("마감 지난 공고도 보기", value=(qp.get("exp") == "1"))

# 마감 지난 공고 숨기기
base = notices
if not show_expired:
    today = today_kst()
    def not_expired(n):
        dl = n.get("deadline")
        if not dl:
            return True
        try:
            return date.fromisoformat(str(dl)[:10]) >= today
        except Exception:
            return True
    base = [n for n in notices if not_expired(n)]

total = len(base)
review = len([n for n in base if n.get("status") != "부적합 제외"])
recommend = len([n for n in base
                 if (n.get("analysis") or {}).get("recommendation") == "참여 권장"])
st.markdown(f"### 전체 {total}건  ·  검토 대상 {review}건  ·  참여 권장 {recommend}건")

rows = base
if f == "검토 대상만":
    rows = [n for n in base if n.get("status") != "부적합 제외"]
elif f == "부적합 제외":
    rows = [n for n in base if n.get("status") == "부적합 제외"]
elif f == "참여 권장만":
    rows = [n for n in base
            if (n.get("analysis") or {}).get("recommendation") == "참여 권장"]

# --- 정렬 ---
c1, c2 = st.columns([3, 1])
sort_key = c1.radio("정렬 기준", SORTS, horizontal=True,
                    index=SORTS.index(qp.get("s")) if qp.get("s") in SORTS else 0)
sort_desc = c2.radio("순서", ["내림차순", "오름차순"], horizontal=True,
                     index=1 if qp.get("d") == "asc" else 0) == "내림차순"

# 현재 선택을 URL에 저장 (상세 다녀와도 복원)
st.query_params["f"] = f
st.query_params["s"] = sort_key
st.query_params["d"] = "desc" if sort_desc else "asc"
st.query_params["exp"] = "1" if show_expired else "0"

FIELD = {"게시일자": "posted_at", "입찰마감일": "deadline",
         "검토점수": "quant_score", "사업예산": "budget"}
field = FIELD[sort_key]

def sort_value(n):
    v = n.get(field)
    if v is None:
        return (1, 0 if field in ("quant_score", "budget") else "")
    return (0, v)

rows = sorted(rows, key=sort_value, reverse=sort_desc)

st.write(f"**{len(rows)}건**")
st.divider()

for n in rows:
    col1, col2, col3, col4, col5, col6 = st.columns([3.5, 1.8, 1.2, 0.9, 1, 1.4])

    url = n.get("source_url")
    mark = " ⚠️" if (n.get("analysis") or {}).get("needs_check") else ""
    if url:
        col1.markdown(f"**[{n['title']}]({url})**{mark}")
    else:
        col1.markdown(f"**{n['title']}**{mark}")

    # 상세보기: 버튼 대신 링크 → Ctrl(Cmd)+클릭으로 새 탭 열기 가능
    col1.markdown(f"[▶ 분석 보기](?id={n['id']})")

    col2.write(n.get("agency", "-"))

    b = n.get("budget")
    if b:
        b = int(b)
        col3.write(f"{b/100000000:.1f}억" if b >= 100000000 else f"{b//10000:,}만")
    else:
        col3.write("-")

    col4.write(f"{n.get('quant_score','-')}점")
    col5.write((n.get("deadline") or "-")[:10])

    # 메인에서 바로 상태 변경
    cur = n.get("status", "검토 대기")
    new = col6.selectbox(
        "상태", STATUS_OPTIONS,
        index=STATUS_OPTIONS.index(cur) if cur in STATUS_OPTIONS else 0,
        key=f"st_{n['id']}", label_visibility="collapsed",
    )
    if new != cur:
        supabase.table("notices").update({"status": new}).eq("id", n["id"]).execute()
        st.cache_data.clear()
        st.rerun()

    st.divider()
