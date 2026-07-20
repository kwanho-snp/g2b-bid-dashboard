import streamlit as st
from supabase import create_client
from datetime import datetime

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="입찰공고 검토 보드", layout="wide")

@st.cache_data(ttl=30)
def load_notices():
    res = supabase.table("notices").select("*").order("posted_at", desc=True).execute()
    return res.data

def load_attachments(notice_id):
    res = supabase.table("attachments").select("*").eq("notice_id", notice_id).execute()
    return res.data

STATUS_OPTIONS = ["검토 대기", "검토중", "참여 결정", "미참여", "부적합 제외"]
notices = load_notices()

params = st.query_params
if "id" in params:
    nid = params["id"]
    notice = next((n for n in notices if n["id"] == nid), None)
    if notice:
        if st.button("← 목록으로"):
            st.query_params.clear()
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
        st.info(f"**추천: {analysis.get('recommendation','-')}**")

        ts = notice.get("task_summary") or {}
        if ts:
            st.subheader("과업 요약")
            st.write(ts.get("overview", ""))
            for t in ts.get("tasks", []):
                if isinstance(t, dict):
                    st.write("- " + t.get("title","") + ": " + t.get("detail",""))
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
                icon = {"통과":"✅","탈락":"❌","판단불가":"❓"}.get(d.get("result"), "•")
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

# ===== 리스트 화면 =====
st.title("📋 입찰공고 검토 보드")

total = len(notices)
review = len([n for n in notices if n.get("status") != "부적합 제외"])
recommend = len([n for n in notices if (n.get("analysis") or {}).get("recommendation") == "참여 권장"])
st.markdown(f"### 전체 {total}건  ·  검토 대상 {review}건  ·  참여 권장 {recommend}건")

f = st.radio("필터", ["전체", "검토 대상만", "부적합 제외", "참여 권장만"], horizontal=True)
show_expired = st.checkbox("마감 지난 공고도 보기", value=False)

rows = notices
if f == "검토 대상만":
    rows = [n for n in notices if n.get("status") != "부적합 제외"]
elif f == "부적합 제외":
    rows = [n for n in notices if n.get("status") == "부적합 제외"]
elif f == "참여 권장만":
    rows = [n for n in notices if (n.get("analysis") or {}).get("recommendation") == "참여 권장"]

# 마감 지난 공고 숨기기 (체크박스 켜면 포함)
if not show_expired:
    now = datetime.now()
    def not_expired(n):
        dl = n.get("deadline")
        if not dl:
            return True
        try:
            return datetime.fromisoformat(dl.replace("Z", "")) >= now
        except Exception:
            return True
    rows = [n for n in rows if not_expired(n)]

st.write(f"**{len(rows)}건**")
st.divider()

for n in rows:
    col1, col2, col3, col4, col5 = st.columns([4, 2, 1, 1, 1])
    url = n.get("source_url")
    if url:
        col1.markdown(f"**[{n['title']}]({url})**")
    else:
        col1.markdown(f"**{n['title']}**")
    col2.write(n.get("agency", "-"))
    col3.write(f"{n.get('quant_score','-')}점")
    col4.write((n.get("deadline") or "-")[:10])
    col5.write(n.get("status", "-"))
    if col1.button("상세보기", key=n["id"]):
        st.query_params["id"] = n["id"]
        st.rerun()
    st.divider()
