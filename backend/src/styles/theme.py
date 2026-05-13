def get_css() -> str:
    return """
<style>
/* ── Reset & base ── */
*, *::before, *::after { box-sizing: border-box; }

.stApp {
    background-color: #0a0a0a !important;
    color: #FFFFFF;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

#MainMenu, footer, header { visibility: hidden; }

section[data-testid="stSidebar"] { display: none; }

.block-container {
    max-width: 1280px !important;
    padding-top: 1.5rem !important;
    padding-bottom: 3rem !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: #111; }
::-webkit-scrollbar-thumb { background: #2a2a2a; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #00E676; }

/* ── Streamlit widget overrides ── */
.stTextInput input, .stSelectbox select {
    background: #111111 !important;
    border: 1px solid #1e1e1e !important;
    border-radius: 8px !important;
    color: #FFFFFF !important;
    transition: border-color 0.2s;
}
.stTextInput input:focus {
    border-color: #00E676 !important;
    box-shadow: 0 0 0 2px rgba(0,230,118,0.15) !important;
}
.stTextInput label, .stSelectbox label {
    color: #AAAAAA !important;
    font-size: 12px !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
.stButton button {
    background: #111111 !important;
    border: 1px solid #1e1e1e !important;
    color: #FFFFFF !important;
    border-radius: 8px !important;
    transition: all 0.2s !important;
    font-size: 13px !important;
}
.stButton button:hover {
    border-color: #00E676 !important;
    color: #00E676 !important;
    background: rgba(0,230,118,0.05) !important;
}
.stButton button:active {
    transform: translateY(1px) !important;
}
div[data-testid="stHorizontalBlock"] { gap: 12px; }

/* ── App header ── */
.app-header {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 0 0 18px 0;
    border-bottom: 1px solid #1e1e1e;
    margin-bottom: 20px;
}
.logo-mark {
    width: 38px;
    height: 38px;
    background: linear-gradient(135deg, #00E676 0%, #00BF60 100%);
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
    font-weight: 900;
    color: #0a0a0a;
    flex-shrink: 0;
}
.app-title {
    font-size: 22px;
    font-weight: 800;
    color: #FFFFFF;
    letter-spacing: -0.02em;
}
.app-subtitle {
    font-size: 12px;
    color: #AAAAAA;
    margin-top: 2px;
}
.app-header-spacer { flex: 1; }
.tour-badge {
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
}
.tour-atp { background: rgba(0,230,118,0.15); color: #00E676; border: 1px solid rgba(0,230,118,0.3); }
.tour-wta { background: rgba(255,68,120,0.15); color: #FF4478; border: 1px solid rgba(255,68,120,0.3); }

/* ── Tour toggle ── */
.tour-toggle-row {
    display: flex;
    gap: 8px;
    margin-bottom: 20px;
}

/* ── Custom tab nav ── */
.tab-nav-row {
    display: flex;
    gap: 4px;
    border-bottom: 1px solid #1e1e1e;
    margin-bottom: 24px;
}
.tab-nav-btn {
    padding: 10px 18px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid transparent;
    border-bottom: none;
    border-radius: 8px 8px 0 0;
    background: transparent;
    color: #AAAAAA;
    transition: all 0.2s;
    white-space: nowrap;
    min-width: 44px;
}
.tab-nav-btn.active {
    background: #111111;
    border-color: #1e1e1e;
    color: #00E676;
    border-bottom: 1px solid #111111;
    margin-bottom: -1px;
}
.tab-nav-btn:active { transform: translateY(1px); }

/* ── Stat card (3D tilt target) ── */
.stat-card {
    background: #111111;
    border: 1px solid #1e1e1e;
    border-radius: 12px;
    padding: 18px;
    position: relative;
    cursor: default;
    will-change: transform;
    transform-style: preserve-3d;
    transition: box-shadow 0.3s ease;
    overflow: hidden;
    margin-bottom: 12px;
}
.stat-card::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(
        circle at var(--mx, 50%) var(--my, 50%),
        rgba(0,230,118,0.07) 0%,
        transparent 65%
    );
    pointer-events: none;
    border-radius: 12px;
    opacity: 0;
    transition: opacity 0.3s;
}
.stat-card:hover::before { opacity: 1; }
.stat-card:hover {
    box-shadow: 0 12px 40px rgba(0,0,0,0.5), 0 0 0 1px rgba(0,230,118,0.08);
}

/* ── Stat content ── */
.stat-label {
    font-size: 11px;
    color: #AAAAAA;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 6px;
}
.stat-value {
    font-size: 26px;
    font-weight: 700;
    color: #FFFFFF;
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
}
.stat-value .unit {
    font-size: 14px;
    font-weight: 400;
    color: #AAAAAA;
    margin-left: 2px;
}
.stat-accent { color: #00E676; }
.stat-warn { color: #FF4444; }
.stat-sub {
    font-size: 11px;
    color: #555;
    margin-top: 4px;
}

/* ── Section header ── */
.section-header {
    font-size: 13px;
    font-weight: 600;
    color: #AAAAAA;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin: 24px 0 12px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.section-header::after {
    content: '';
    flex: 1;
    height: 1px;
    background: #1e1e1e;
}

/* ── Archetype badge ── */
.archetype-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 14px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

/* ── Surface badge ── */
.surface-badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
.sb-hard { background: #1565C0; color: #fff; }
.sb-clay { background: #BF360C; color: #fff; }
.sb-grass { background: #2E7D32; color: #fff; }
.sb-unknown { background: #333; color: #aaa; }

/* ── Form dots ── */
.form-row { display: flex; gap: 5px; flex-wrap: wrap; align-items: center; }
.form-dot {
    width: 13px; height: 13px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
}
.form-w { background: #00E676; }
.form-l { background: #FF4444; }

/* ── Result pills ── */
.pill-w {
    display: inline-block;
    background: rgba(0,230,118,0.15);
    color: #00E676;
    border: 1px solid rgba(0,230,118,0.3);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
}
.pill-l {
    display: inline-block;
    background: rgba(255,68,68,0.15);
    color: #FF4444;
    border: 1px solid rgba(255,68,68,0.3);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
}

/* ── Player selected display ── */
.player-chip {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    background: #111111;
    border: 1px solid rgba(0,230,118,0.35);
    border-radius: 10px;
    margin-bottom: 16px;
}
.player-chip-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: #00E676;
    flex-shrink: 0;
    box-shadow: 0 0 6px rgba(0,230,118,0.6);
}
.player-chip-name {
    font-size: 14px;
    font-weight: 600;
    color: #FFFFFF;
}
.player-chip-clear {
    margin-left: auto;
    font-size: 11px;
    color: #555;
    cursor: pointer;
}

/* ── Search results ── */
.search-result-item {
    padding: 10px 14px;
    background: #111111;
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.15s;
    margin-bottom: 4px;
    font-size: 13px;
    color: #FFFFFF;
}
.search-result-item:hover {
    border-color: #00E676;
    background: rgba(0,230,118,0.05);
}
.search-result-gender { font-size: 10px; color: #AAAAAA; margin-left: 6px; }

/* ── Over/Under lean badge ── */
.lean-badge {
    display: inline-block;
    padding: 8px 22px;
    border-radius: 8px;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 0.04em;
    animation: flipY 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}
@keyframes flipY {
    from { transform: rotateY(90deg) scale(0.8); opacity: 0; }
    to   { transform: rotateY(0deg)  scale(1);   opacity: 1; }
}
.lean-over { background: linear-gradient(135deg,#00E676,#00BF60); color: #0a0a0a; }
.lean-under { background: linear-gradient(135deg,#FF4444,#CC0000); color: #fff; }
.lean-neutral { background: #222; color: #AAAAAA; }

/* ── Projection display ── */
.proj-number {
    font-size: 54px;
    font-weight: 900;
    color: #00E676;
    text-align: center;
    font-variant-numeric: tabular-nums;
    line-height: 1;
    text-shadow: 0 0 40px rgba(0,230,118,0.3);
}
.proj-label {
    text-align: center;
    font-size: 12px;
    color: #AAAAAA;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 4px;
}

/* ── Confidence bar ── */
.conf-bar-wrap {
    background: #1a1a1a;
    border-radius: 4px;
    height: 6px;
    margin: 8px 0 4px;
    overflow: hidden;
}
.conf-bar {
    height: 6px;
    border-radius: 4px;
    background: linear-gradient(90deg, #00E676, #00BF60);
    transition: width 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.conf-label {
    font-size: 11px;
    color: #AAAAAA;
    text-align: right;
}

/* ── AI writeup card ── */
.ai-card {
    background: rgba(0,230,118,0.03);
    border: 1px solid rgba(0,230,118,0.18);
    border-radius: 12px;
    padding: 20px 24px;
    position: relative;
    margin-top: 16px;
}
.ai-card-label {
    position: absolute;
    top: 12px;
    right: 16px;
    font-size: 10px;
    color: rgba(0,230,118,0.6);
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
}
.ai-card-text {
    font-size: 14px;
    line-height: 1.7;
    color: #DDDDDD;
}

/* ── Speed tier card ── */
.speed-card {
    background: #111111;
    border: 1px solid #1e1e1e;
    border-radius: 10px;
    padding: 14px;
    text-align: center;
    margin-bottom: 12px;
}
.speed-card-label { font-size: 11px; color: #AAAAAA; text-transform: uppercase; letter-spacing: 0.06em; }
.speed-card-value { font-size: 18px; font-weight: 700; color: #FFFFFF; margin: 4px 0 2px; }
.speed-card-cpr { font-size: 12px; color: #00E676; }

/* ── H2H table ── */
.h2h-table-wrap { overflow-x: auto; }
.h2h-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
.h2h-table th {
    background: #0d0d0d;
    color: #AAAAAA;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 10px 12px;
    text-align: left;
    border-bottom: 1px solid #1e1e1e;
}
.h2h-table td {
    padding: 10px 12px;
    border-bottom: 1px solid #151515;
    color: #FFFFFF;
    vertical-align: middle;
    transition: transform 0.15s, background 0.15s;
}
.h2h-table tr:hover td {
    background: rgba(0,230,118,0.03);
    transform: translateY(-1px);
}

/* ── No data placeholder ── */
.no-data {
    text-align: center;
    padding: 48px 20px;
    color: #444;
    font-size: 14px;
}
.no-data .icon { font-size: 32px; margin-bottom: 12px; }
.no-data .msg { color: #555; }

/* ── Probability bar ── */
.prob-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 6px 0;
    font-size: 13px;
}
.prob-label { width: 180px; color: #AAAAAA; flex-shrink: 0; font-size: 12px; }
.prob-track {
    flex: 1;
    background: #1a1a1a;
    border-radius: 3px;
    height: 5px;
    overflow: hidden;
}
.prob-fill { height: 5px; border-radius: 3px; background: #00E676; }
.prob-val { width: 48px; text-align: right; color: #FFFFFF; font-size: 12px; font-variant-numeric: tabular-nums; }

/* ── Divider ── */
.divider {
    height: 1px;
    background: linear-gradient(90deg, transparent, #1e1e1e 20%, #1e1e1e 80%, transparent);
    margin: 20px 0;
}

/* ── Empty state ── */
.empty-prompt {
    border: 1px dashed #1e1e1e;
    border-radius: 12px;
    padding: 48px 24px;
    text-align: center;
    color: #444;
}
.empty-prompt .ep-icon { font-size: 40px; margin-bottom: 12px; }
.empty-prompt .ep-title { font-size: 16px; color: #666; font-weight: 600; margin-bottom: 6px; }
.empty-prompt .ep-sub { font-size: 13px; color: #444; }

/* ── Responsive ── */
@media (max-width: 767px) {
    .stat-value { font-size: 20px; }
    .proj-number { font-size: 38px; }
    .tab-nav-row { overflow-x: auto; flex-wrap: nowrap; padding-bottom: 2px; }
    .tab-nav-btn { padding: 8px 12px; font-size: 12px; }
    .block-container { padding-left: 12px !important; padding-right: 12px !important; }
    .h2h-table-wrap { overflow-x: auto; }
    .stButton button { min-height: 44px !important; }
}
@media (min-width: 768px) and (max-width: 1199px) {
    .stat-value { font-size: 22px; }
}
</style>
"""


def get_3d_js() -> str:
    return """
<script>
(function() {
    if ('ontouchstart' in window || navigator.maxTouchPoints > 0) return;

    const MAX_TILT = 12;

    function attachTilt(card) {
        if (card._tilt) return;
        card._tilt = true;

        card.addEventListener('mousemove', function(e) {
            const r = card.getBoundingClientRect();
            const x = e.clientX - r.left;
            const y = e.clientY - r.top;
            const cx = r.width / 2;
            const cy = r.height / 2;
            const rx = ((y - cy) / cy) * -MAX_TILT;
            const ry = ((x - cx) / cx) * MAX_TILT;
            card.style.transition = 'box-shadow 0.1s';
            card.style.transform = `perspective(900px) rotateX(${rx}deg) rotateY(${ry}deg) translateZ(5px)`;
            card.style.boxShadow = `${-ry * 0.8}px ${rx * 0.8}px 28px rgba(0,0,0,0.55), 0 0 0 1px rgba(0,230,118,0.07)`;
            card.style.setProperty('--mx', `${(x / r.width) * 100}%`);
            card.style.setProperty('--my', `${(y / r.height) * 100}%`);
        });

        card.addEventListener('mouseleave', function() {
            card.style.transition = 'transform 0.35s ease-out, box-shadow 0.35s ease-out';
            card.style.transform = 'perspective(900px) rotateX(0deg) rotateY(0deg) translateZ(0)';
            card.style.boxShadow = '';
        });
    }

    function scanCards() {
        document.querySelectorAll('.stat-card').forEach(attachTilt);
    }

    setTimeout(scanCards, 400);
    new MutationObserver(() => setTimeout(scanCards, 80))
        .observe(document.body, { childList: true, subtree: true });
})();
</script>
"""
