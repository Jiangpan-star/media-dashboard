"""
融媒体数据看板 - 本地飞书数据服务 v3.0
运行：python app.py
访问：http://localhost:8765
汇总数据从四平台分表实时聚合（绕过150条汇总表限制）
"""

import sys
import io
# 强制 stdout/stderr 使用 UTF-8，防止 Windows GBK 终端下 emoji 导致 UnicodeEncodeError
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import json
import re
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request
import threading

# ─── 配置 ───────────────────────────────────────────────
# 优先从 config.py 读取（包含真实密钥，请勿提交到 Git）
try:
    from config import (
        APP_ID, APP_SECRET, APP_TOKEN,
        TABLE_SUMMARY, TABLE_DOUYIN, TABLE_KS, TABLE_BILI, TABLE_WX, TABLE_TASK
    )
except ImportError:
    # 回退：从环境变量读取（部署环境使用）
    import os
    APP_ID     = os.environ.get('FEISHU_APP_ID',     'your_app_id_here')
    APP_SECRET = os.environ.get('FEISHU_APP_SECRET', 'your_app_secret_here')
    APP_TOKEN  = os.environ.get('FEISHU_APP_TOKEN',  'your_app_token_here')
    TABLE_SUMMARY = os.environ.get('TABLE_SUMMARY', 'tblHMOnE2BItPgBX')
    TABLE_DOUYIN  = os.environ.get('TABLE_DOUYIN',  'tbllvGQssUljleKd')
    TABLE_KS      = os.environ.get('TABLE_KS',      'tblB0pdwwwqwntVW')
    TABLE_BILI    = os.environ.get('TABLE_BILI',    'tblR4U14SrHv3mAv')
    TABLE_WX      = os.environ.get('TABLE_WX',      'tbl3gP6AK5vULPkD')
    TABLE_TASK    = os.environ.get('TABLE_TASK',    'tblSkGhN53t0JCJm')

PORT      = 8765
CACHE_TTL = 300  # 5 分钟缓存
# ────────────────────────────────────────────────────────

_cache_summary   = {'data': None, 'ts': 0}
_cache_platform  = {'data': None, 'ts': 0}
_cache_tasks     = {'data': None, 'ts': 0}
_cache_trending  = {'data': None, 'ts': 0}
_author_map      = None   # {标准化标题: 作者名}
_desc_map       = {}     # {标准化标题: 原始标题}
_author_map_ts  = 0


# ═══════════════════════════════════════════════════════
# 基础网络请求
# ═══════════════════════════════════════════════════════

def feishu_post(url, payload):
    data = json.dumps(payload).encode('utf-8')
    req = Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode('utf-8'))

def feishu_get(url, token):
    req = Request(url, headers={'Authorization': f'Bearer {token}'})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode('utf-8'))

def get_tenant_token():
    result = feishu_post(
        'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        {'app_id': APP_ID, 'app_secret': APP_SECRET}
    )
    if result.get('code') != 0:
        raise RuntimeError(f"获取token失败: {result}")
    return result['tenant_access_token']

def fetch_all_records(token, table_id):
    records, page_token, page = [], None, 0
    while True:
        page += 1
        url = (f'https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}'
               f'/tables/{table_id}/records?page_size=500')
        if page_token:
            url += f'&page_token={page_token}'
        result = feishu_get(url, token)
        if result.get('code') != 0:
            print(f"  [WARN] 获取记录失败({table_id}): {result}")
            break
        items = result.get('data', {}).get('items', [])
        records.extend(items)
        has_more = result.get('data', {}).get('has_more', False)
        page_token = result.get('data', {}).get('page_token')
        print(f"  [{table_id[:8]}] 第{page}页: {len(items)} 条，累计 {len(records)} 条")
        if not has_more:
            break
    return records


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def num(v):
    if v is None: return 0
    if isinstance(v, list): return sum(num(x) for x in v)
    s = str(v).replace('%', '').replace(',', '').strip()
    try: return float(s)
    except: return 0

def parse_ts(v):
    """返回毫秒时间戳，兼容列表包装格式"""
    if not v: return 0
    # 飞书日期字段常见格式：[毫秒时间戳] 列表
    if isinstance(v, list):
        v = v[0] if v else 0
    if isinstance(v, (int, float)): return int(v)
    # "2025年09月30日 ..."
    m = re.match(r'(\d{4})年(\d{2})月(\d{2})日', str(v))
    if m:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return int(dt.timestamp() * 1000)
    # "2025-09-30"
    m2 = re.match(r'(\d{4})-(\d{2})-(\d{2})', str(v))
    if m2:
        dt = datetime(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
        return int(dt.timestamp() * 1000)
    return 0

def ts_to_month(ts_ms):
    if not ts_ms: return None
    try:
        return datetime.fromtimestamp(ts_ms / 1000).strftime('%Y-%m')
    except:
        return None

def title_str(v):
    if isinstance(v, list):
        return ' '.join(str(x.get('text', x)) if isinstance(x, dict) else str(x) for x in v)
    return str(v or '')

def author_str(v):
    if isinstance(v, list):
        return ', '.join(x.get('name', x.get('text', str(x))) if isinstance(x, dict) else str(x) for x in v)
    return str(v or '')


# ═══════════════════════════════════════════════════════
# 任务管理数据处理
# ═══════════════════════════════════════════════════════

def _parse_select(v):
    """解析单选/查岗字段"""
    if not v: return ''
    if isinstance(v, list): return v[0].get('text', str(v[0])) if v else ''
    return str(v)

def _parse_members(v):
    """解析多选字段"""
    if not v: return ''
    if isinstance(v, list):
        return ', '.join(x.get('text', str(x)) if isinstance(x, dict) else str(x) for x in v)
    return str(v)

def _parse_date(v):
    """解析飞书日期字段（毫秒时间戳）"""
    if not v: return ''
    try:
        return datetime.fromtimestamp(int(v) / 1000).strftime('%Y-%m-%d')
    except:
        return ''

def process_tasks(records):
    """标准化任务记录"""
    tasks = []
    for i, rec in enumerate(records, 1):
        f = rec['fields']
        tasks.append({
            'id':           i,
            'title':        _parse_select(f.get('选题')),
            'type':         _parse_select(f.get('内容形式')),
            'lead':         _parse_select(f.get('牵头人员')),
            'members':      _parse_members(f.get('参与人员')),
            'status':       _parse_select(f.get('进展')),
            'start_date':   _parse_date(f.get('开始日期')),
            'due_date':     _parse_date(f.get('预计交付日期')),
            'publish_date': _parse_date(f.get('预计发布日期')),
            'done_date':    _parse_date(f.get('实际完成日期')),
            'progress':     str(f.get('最新进展记录', '')),
            'code':         str(f.get('内容编号', '')),
            'remark':       str(f.get('备注', '')),
        })
    return tasks

def _normalize_for_match(s):
    """标准化标题用于模糊匹配：去掉括号编号、特殊符号，统一空白"""
    import re
    s = str(s).strip()
    s = re.sub(r'（\s*第[一二三四五六七八九十百\d]+[期弹部集]?\s*）', '', s)
    s = re.sub(r'\(\s*第[一二三四五六七八九十百\d]+[期弹部集]?\s*\)', '', s)
    s = re.sub(r'[（(][^)()]*[))]', '', s)
    s = re.sub(r'[#@]"', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def _extract_author_from_desc(desc):
    """从视频描述文本中正则解析作者名"""
    import re
    if not desc:
        return ''
    patterns = [
        r'拍摄[/\s]?制作[：:]\s*([^\s，。,，]+)',
        r'视频制作[：:]\s*([^\s，。,，]+)',
        r'制作[：:]\s*([^\s，。,，]+)',
        r'拍摄[：:]\s*([^\s，。,，]+)',
        r'文案[：:]\s*([^\s，。,，]+)',
    ]
    for pat in patterns:
        m = re.search(pat, desc)
        if m:
            name = m.group(1).strip()
            # 过滤掉话题标签
            if name and not name.startswith('#') and len(name) <= 6:
                return name
    return ''

def _fuzzy_match_author(task_title, author_map, desc_map):
    """模糊匹配：任务标题→视频描述，返回作者名
       author_map: {视频描述: 第一作者} 来自汇总表
       desc_map:   {标准化描述: 原始描述} 用于额外解析
    """
    import re
    tt = task_title.strip()
    if not tt:
        return ''
    tt_norm = _normalize_for_match(tt)

    candidates = []
    for desc, author in author_map.items():
        dn = _normalize_for_match(desc)
        # 精确标准化匹配
        if tt_norm == dn and dn:
            return author
        # 标准化后标题在描述中
        if tt_norm and tt_norm in dn:
            pos = dn.index(tt_norm)
            score = len(tt_norm) / max(len(dn), 1)
            candidates.append((score, pos, author, desc))
        # 标准化后描述在标题中（降权）
        elif dn and dn in tt_norm:
            score = len(dn) / max(len(tt_norm), 1) * 0.7
            candidates.append((score, 0, author, desc))

    if candidates:
        # 过滤掉明显误匹配（标题太短却在长描述中间的情况）
        valid = []
        for score, pos, author, desc in candidates:
            # 短标题（<5字）要求位置靠前或覆盖率>30%
            if len(tt_norm) < 5:
                if pos < 10 or score > 0.3:
                    valid.append((score, pos, author))
            else:
                valid.append((score, pos, author))
        if valid:
            valid.sort(key=lambda x: (-x[0], x[1]))
            return valid[0][2]
        # 如果没有valid但有candidates，取最高分
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][2]

    return ''

def _build_author_map():
    """从汇总表+四平台分表拉取记录，建立 {标题: 第一作者} 映射
       同时建立 {标准化标题: 原始描述} 用于描述匹配
    """
    import re
    try:
        token = get_tenant_token()
        all_records = []

        # 1. 汇总表
        summary_recs = fetch_all_records(token, TABLE_SUMMARY)
        for rec in summary_recs:
            f = rec.get('fields', {})
            title = title_str(f.get('视频描述', '') or f.get('标题', '')).strip()
            author = author_str(f.get('第一作者', '') or f.get('作者', '')).strip()
            if title:
                all_records.append({'title': title, 'author': author})

        # 2. 四平台分表（仅取标题+从描述解析作者）
        for table_id, name_key in [
            (TABLE_DOUYIN, '作品名称'),
            (TABLE_KS, '作品'),
            (TABLE_BILI, '视频标题'),
            (TABLE_WX, '视频描述'),
        ]:
            recs = fetch_all_records(token, table_id)
            for rec in recs:
                f = rec.get('fields', {})
                title = title_str(f.get(name_key, '')).strip()
                if not title:
                    continue
                # 汇总表已明确有作者就用它，没有则从描述解析
                desc = title
                if table_id == TABLE_WX:
                    desc = title
                author = _extract_author_from_desc(desc)
                if title and author:
                    all_records.append({'title': title, 'author': author})

        # 建立 {标题: 作者} 映射，标题去重（保留有作者信息的）
        author_map = {}
        desc_map = {}
        for rec in all_records:
            if rec['title'] and rec['author']:
                author_map[rec['title']] = rec['author']
            if rec['title']:
                desc_map[_normalize_for_match(rec['title'])] = rec['title']

        print(f"  [OK] 全量作者映射: {len(author_map)} 条（含{len(desc_map)}个描述）")
        return author_map, desc_map
    except Exception as e:
        print(f"  [WARN] 作者映射构建失败: {e}")
        return {}, {}

def _fuzzy_match_author2(task_title, author_map, desc_map):
    """使用全量映射的模糊匹配"""
    tt = task_title.strip()
    if not tt:
        return ''
    tt_norm = _normalize_for_match(tt)

    candidates = []
    for desc_norm, author in [(k, author_map.get(desc_map[k], '')) for k in desc_map]:
        if not author:
            continue
        # 精确标准化匹配
        if tt_norm == desc_norm and desc_norm:
            return author
        # 标准化后标题在描述中
        if tt_norm and tt_norm in desc_norm:
            pos = desc_norm.index(tt_norm)
            score = len(tt_norm) / max(len(desc_norm), 1)
            candidates.append((score, pos, author))
        # 标准化后描述在标题中（降权）
        elif desc_norm and desc_norm in tt_norm:
            score = len(desc_norm) / max(len(tt_norm), 1) * 0.7
            candidates.append((score, 0, author))

    if candidates:
        valid = []
        for score, pos, author in candidates:
            if len(tt_norm) < 5:
                if pos < 10 or score > 0.3:
                    valid.append((score, pos, author))
            else:
                valid.append((score, pos, author))
        if valid:
            valid.sort(key=lambda x: (-x[0], x[1]))
            return valid[0][2]
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][2]
    return ''

def get_trending_data(force=False):
    """抓取实时热搜（百度热搜 + 微博热搜），返回热点话题与选题建议"""
    global _cache_trending
    now = time.time()
    # 热搜每 10 分钟刷新一次
    if not force and _cache_trending.get('data') and (now - _cache_trending.get('ts', 0)) < 600:
        return _cache_trending['data']

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 抓取实时热搜...")

    items = []

    # ── 1. 百度热搜 ──────────────────────────────────────
    try:
        req = Request(
            'https://top.baidu.com/board?tab=realtime',
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://www.baidu.com/',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            }
        )
        with urlopen(req, timeout=8) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        # 解析热搜词：从 JSON 片段或 HTML 中提取
        # 百度热搜榜数据嵌在 window.__INITIAL_STATE__ 或 data-raw 中
        matches = re.findall(r'"query"\s*:\s*"([^"]{2,30})"', html)
        # 热度值
        hot_vals = re.findall(r'"hotScore"\s*:\s*(\d+)', html)
        if not matches:
            # 备用：从 title 标签附近抓
            matches = re.findall(r'class="c-single-text-ellipsis"[^>]*>([^<]{2,25})<', html)
        seen = set()
        for i, kw in enumerate(matches[:15]):
            kw = kw.strip()
            if not kw or kw in seen:
                continue
            seen.add(kw)
            hot = int(hot_vals[i]) if i < len(hot_vals) else 0
            items.append({
                'keyword': kw,
                'hot': hot,
                'source': '百度热搜',
                'rank': len(items) + 1,
            })
        print(f"  [百度热搜] 获取 {len(items)} 条")
    except Exception as e:
        print(f"  [WARN] 百度热搜失败: {e}")

    # ── 2. 微博热搜（备用数据源）──────────────────────────
    if len(items) < 8:
        try:
            req2 = Request(
                'https://weibo.com/ajax/side/hotSearch',
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': 'https://weibo.com/',
                    'Accept': 'application/json, text/plain, */*',
                }
            )
            with urlopen(req2, timeout=8) as resp:
                wb_data = json.loads(resp.read().decode('utf-8'))
            wb_list = wb_data.get('data', {}).get('realtime', [])
            seen_wb = {it['keyword'] for it in items}
            for entry in wb_list[:15]:
                kw = entry.get('word', '').strip()
                if not kw or kw in seen_wb:
                    continue
                seen_wb.add(kw)
                hot = int(entry.get('num', 0))
                items.append({
                    'keyword': kw,
                    'hot': hot,
                    'source': '微博热搜',
                    'rank': len(items) + 1,
                })
                if len(items) >= 15:
                    break
            print(f"  [微博热搜] 补充后共 {len(items)} 条")
        except Exception as e:
            print(f"  [WARN] 微博热搜失败: {e}")

    # ── 3. 如果以上都失败，使用应急备用数据 ─────────────────
    if not items:
        items = _get_fallback_trending()
        print(f"  [备用] 返回预置热搜 {len(items)} 条")

    # ── 4. 为每个热点生成选题建议（高校融媒视角）────────────
    for it in items:
        it['suggestion'] = _gen_topic_suggestion(it['keyword'])

    data = {
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'items': items[:12],
        'count': len(items),
    }
    _cache_trending['data'] = data
    _cache_trending['ts'] = now
    print(f"  [OK] 热搜数据完成: {len(items)} 条")
    return data


def _get_fallback_trending():
    """应急备用热搜（当网络抓取失败时使用）"""
    now = datetime.now()
    month = now.month
    # 根据月份返回季节性相关话题
    seasonal = []
    if 2 <= month <= 4:
        seasonal = ['春季招聘', '大学生就业', '研究生复试']
    elif 5 <= month <= 6:
        seasonal = ['高考备战', '毕业季', '大学生创新创业']
    elif 7 <= month <= 8:
        seasonal = ['暑假旅游', '大学生暑期实践', '考研备考']
    elif 9 <= month <= 10:
        seasonal = ['开学季', '国庆出行', '大学生活']
    else:
        seasonal = ['期末备考', '年终总结', '元旦跨年']

    base = [
        {'keyword': kw, 'hot': 500000 - i * 30000, 'source': '应急备用', 'rank': i + 1}
        for i, kw in enumerate(seasonal + ['人工智能', '乡村振兴', '民族文化', '校园生活', '体育健身'])
    ]
    return base[:12]


def _gen_topic_suggestion(keyword):
    """根据热点关键词，结合西北民大新媒体中心实际内容风格生成选题建议
    
    内容风格参考（来自历史选题库）：
    - 校园风景美拍系列：春/夏/秋/冬日民大、用XX打开民大、色轮/像素/彩带/赛博民大
    - 节日/节点：高考倒计时、考研倒计时、毕业季、节日祝福
    - 活动记录：运动会、迎新、晚会、讲座活动
    - 美食/探索：舌尖上的民大、寻味民大系列
    - 人文故事：校园人物推文、民大er系列
    - 创意拍摄：一镜到底、影视飓风风格、积木/纸张/镜像民大
    """
    kw = keyword.strip()

    # ── 过滤：政治/外交/军事/灾难类话题 ── 不适合高校新媒体做视频 ──
    skip_keywords = [
        '总统', '主席', '峰会', '外交', '协议', '谈判', '战争', '军事', '制裁',
        '核武', '导弹', '冲突', '伊朗', '俄罗斯', '以色列', '乌克兰', '台湾',
        '政治', '政府', '法院', '判决', '案件', '嫌疑', '枪击', '爆炸', '恐怖',
        '地震', '洪水', '灾害', '遇难', '死亡', '事故', '坠机',
    ]
    if any(sk in kw for sk in skip_keywords):
        return '⚠️ 政治/时政类话题，不适合本号选题，建议跳过'

    # ── 节日 / 节气 ──────────────────────────────────────────
    rules_holiday = [
        (['母亲节', '妇女节', '父亲节'], '节日特辑：拍摄"民大er的妈妈/爸爸们"，用镜头记录民大学子与家人的温情瞬间'),
        (['端午', '粽子'], '节日美食打卡：寻味民大特辑——食堂/周边的端午应季美食探店'),
        (['中秋', '月饼'], '中秋节日特辑：民大校园里的月色风景 + 学子异乡思归的真实故事'),
        (['元旦', '新年', '跨年'], '年终系列：回顾民大这一年的高光时刻，制作年度混剪MV'),
        (['春节', '过年', '除夕', '拜年'], '寒假vlog企划：各地民大学子"过年了！"，多民族春节习俗展示'),
        (['清明'], '清明踏青：春日民大风景拍摄，校园花开打卡地图'),
        (['五四', '青年节'], '五四青年节：寻找民大校园里"闪闪发光"的青年故事，人物推文+短视频'),
        (['劳动节', '五一'], '劳动节特辑：镜头下民大最美劳动者——后勤、保洁、食堂师傅的一天'),
        (['国庆', '十一'], '国庆风景大片：鸟瞰民大、校园国旗前打卡，制作爱国主题混剪'),
        (['圣诞', '平安夜'], '校园圣诞打卡：民大里的圣诞氛围探索，创意拍摄系列'),
        (['重阳', '老人'], '重阳节：走访民大退休教职工，拍摄"民大老人"人物故事'),
        (['教师节'], '教师节特辑：用影视飓风/一镜到底方式拍摄"民大老师的一天"'),
        (['儿童节', '六一'], '六一特辑："民大er的童年"——学生拿出儿时照片，对比现在的校园生活'),
    ]
    for keywords, template in rules_holiday:
        if any(k in kw for k in keywords):
            return template

    # ── 高考 / 考研 / 招生 ───────────────────────────────────
    rules_exam = [
        (['高考倒计时', '高考', '高三'], '高考倒计时系列：延续往年风格，拍摄"我在民大等你"氛围大片，配合倒计时数字推出'),
        (['考研', '研究生'], '考研倒计时系列：记录民大备考学子的冲刺日常，拍摄图书馆/自习室氛围短视频'),
        (['四六级', 'CET', '英语考试'], '四六级温馨小贴士：用创意形式打包考试注意事项，配合校园学习场景拍摄'),
        (['招生', '报考', '填志愿'], '招生季美丽民大系列：校园风景混剪 + 鸟瞰 + 寻味民大美食，展示民大魅力'),
        (['毕业', '毕业季', '学位'], '毕业季混剪：毕业典礼大片 + 毕业祝福视频 + 毕业生的校园最后一天vlog'),
    ]
    for keywords, template in rules_exam:
        if any(k in kw for k in keywords):
            return template

    # ── 天气 / 季节 / 自然 ───────────────────────────────────
    rules_season = [
        (['下雪', '暴雪', '雪', '降雪'], '雪日民大：趁热拍摄雪景大片，推出"冬日民大"系列，银装素裹的校园最出片'),
        (['春天', '春日', '花开', '樱花', '踏青'], '春日民大系列：花开打卡地图 + "用纸袋/课本打开民大的春天"创意拍摄'),
        (['秋天', '秋日', '落叶', '红叶'], '秋日民大：落叶美景大片，延续"XX民大"系列风格，拍摄校园金秋氛围'),
        (['夏天', '高温', '炎热', '避暑'], '夏日民大：食堂冷饮探店 + 校园纳凉圣地打卡，拍摄"夏日民大er怎么过"'),
        (['大风', '沙尘', '降温', '寒潮'], '极端天气下的民大：记录学生应对恶劣天气的真实日常，反差感强出片率高'),
    ]
    for keywords, template in rules_season:
        if any(k in kw for k in keywords):
            return template

    # ── 美食 / 生活 ──────────────────────────────────────────
    rules_food = [
        (['美食', '好吃', '餐厅', '食堂', '外卖', '小吃', '探店'], '寻味民大系列：探索校园食堂新品或周边美食，延续"舌尖上的民大"风格拍摄'),
        (['减肥', '健身', '运动', '锻炼', '体重'], '"民大er，动起来"系列：拍摄校园里的健身达人，展示民大运动场地与氛围'),
        (['睡眠', '熬夜', '作息', '疲惫'], '校园生活写实：记录民大学子的真实作息，"民大er的一天"vlog选题'),
        (['消费', '购物', '种草', '好物'], '"民大好物推荐"：学生分享宿舍必备/学习神器，借热点拍摄生活类内容'),
    ]
    for keywords, template in rules_food:
        if any(k in kw for k in keywords):
            return template

    # ── 校园活动 / 赛事 ─────────────────────────────────────
    rules_event = [
        (['运动会', '田径', '体育赛事', '马拉松', '比赛'], '运动会系列：预告片+开幕式大片+赛后混剪，延续"拍成奥运会"的高质量风格'),
        (['音乐', '演唱会', '歌手', '明星'], '校园音乐氛围：拍摄民大学子日常哼唱/乐器弹奏，或推出校园歌手征集活动'),
        (['舞蹈', '跳舞', '街舞'], '"舞动民大"系列：拍摄校园舞蹈社团或民族舞表演，用转场/创意剪辑出圈'),
        (['展览', '博物馆', '艺术'], '"民大人在哪儿看展"：带大家逛民大博物馆或校内展览，图文+短视频双发'),
        (['志愿者', '公益', '义工'], '志愿者故事：记录民大学子参与志愿活动的暖心瞬间，推出人物推文'),
        (['征兵', '参军', '军训'], '军训/征兵季：拍摄新生军训高光时刻或征兵宣讲现场，制作热血短视频'),
    ]
    for keywords, template in rules_event:
        if any(k in kw for k in keywords):
            return template

    # ── 民族文化 / 地域 ─────────────────────────────────────
    rules_culture = [
        (['民族', '少数民族', '非遗', '传统文化', '民俗', '民族服饰'], '民族文化特辑：展示西北民大的多民族文化氛围，拍摄民族服饰/非遗技艺/民俗活动'),
        (['兰州', '甘肃', '西北', '黄河', '丝绸之路'], '西北在地探索："民大er的兰州打卡地图"或黄河边风景大片，展示西北独特地域美学'),
        (['旅游', '打卡', '出行', '旅行', '风景'], '校园打卡系列：发现民大里的隐藏美景，拍摄"民大一角"或"民大里的圆形/色轮"创意视频'),
    ]
    for keywords, template in rules_culture:
        if any(k in kw for k in keywords):
            return template

    # ── 学业 / 成长 ──────────────────────────────────────────
    rules_study = [
        (['读书', '图书馆', '学习', '看书'], '"请选择你的学习搭子"系列：探访民大图书馆，寻找有趣的学习达人'),
        (['奖学金', '荣誉', '表彰', '获奖'], '高光时刻：记录民大获奖学子的故事，拍摄"民大er闪闪发光"人物推文'),
        (['实习', '就业', '工作', '毕业生'], '毕业去哪儿：采访应届民大毕业生，讲述就业/考研/出国的不同人生选择'),
        (['创业', '创新', '比赛', '竞赛'], '竞赛故事：记录民大学子参加学科竞赛/创业大赛的备战历程'),
    ]
    for keywords, template in rules_study:
        if any(k in kw for k in keywords):
            return template

    # ── 社会热点·通用校园转化 ────────────────────────────────
    rules_social = [
        (['AI', '人工智能', '大模型', 'ChatGPT', '科技'], '"AI建筑生长"类创意视频：用AI工具辅助创作，展示民大建筑/校园的科技美学'),
        (['游戏', '电竞', '二次元', '动漫'], '用游戏/二次元视角打开民大：延续"用星露谷打开民大""洛克王国"等创意拍摄风格'),
        (['宠物', '猫', '狗', '动物'], '民大飞羽/校园动物系列：记录民大的鸟类、流浪猫等校园"野生邻居"故事'),
        (['健康', '医疗', '心理'], '校园心理健康：拍摄民大学子的解压方式，推出"民大er怎么放松"生活类内容'),
        (['直播', '短视频', '流量', '网红'], '"民大脉动"或"无BGM民大"系列：用特别的拍摄方式展现校园真实氛围'),
    ]
    for keywords, template in rules_social:
        if any(k in kw for k in keywords):
            return template

    # ── 通用兜底（基于民大内容风格） ────────────────────────
    import random
    fallback_templates = [
        f'校园美拍：以"{kw}"为主题，用创意构图和转场拍摄一期"XX民大"系列视频',
        f'人物故事：寻找民大校园内与"{kw}"相关的有趣人物，拍摄推文+短视频组合内容',
        f'民大er视角：围绕"{kw}"话题，记录西北民大学子的真实看法与校园生活切片',
        f'寻味民大联动：借助"{kw}"热点，推出一期校园美食或西北特色探店内容',
    ]
    return random.choice(fallback_templates)


def get_task_data(force=False):
    """拉取任务管理数据（带缓存），同时匹配第一作者"""
    global _author_map, _desc_map, _author_map_ts
    now = time.time()
    if not force and _cache_tasks['data'] and (now - _cache_tasks['ts']) < CACHE_TTL:
        print(f"  [缓存] 任务数据（{int(now - _cache_tasks['ts'])}秒前）")
        return _cache_tasks['data']

    # 刷新作者映射（每5分钟刷新一次）
    if not _author_map or (now - _author_map_ts) >= CACHE_TTL:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 刷新作者映射（汇总表+四平台）...")
        _author_map, _desc_map = _build_author_map()
        _author_map_ts = now

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 拉取任务管理数据...")
    try:
        token = get_tenant_token()
        records = fetch_all_records(token, TABLE_TASK)
        tasks = process_tasks(records)

        # 模糊匹配第一作者
        matched = 0
        for t in tasks:
            author = _fuzzy_match_author2(t['title'], _author_map, _desc_map)
            t['author'] = author
            if author:
                matched += 1
        print(f"  [OK] 第一作者匹配: {matched}/{len(tasks)} 条")

        # 统计
        from collections import Counter
        type_count   = Counter(t['type']   for t in tasks)
        status_count = Counter(t['status'] for t in tasks)
        lead_count   = Counter(t['lead']   for t in tasks if t['lead'])

        data = {
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total':      len(tasks),
            'tasks':      tasks,
            'stats': {
                'type_count':   dict(type_count),
                'status_count': dict(status_count),
                'lead_count':   dict(lead_count),
            }
        }
        _cache_tasks['data'] = data
        _cache_tasks['ts']   = now
        print(f"  [OK] 任务数据完成: {len(tasks)} 条")
        return data
    except Exception as e:
        print(f"  [ERR] 任务数据失败: {e}")
        if _cache_tasks['data']:
            return _cache_tasks['data']
        raise



# ═══════════════════════════════════════════════════════
# 汇总表处理（原有逻辑，保留）
# ═══════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════
# 平台数据标准化 → 统一格式
# ═══════════════════════════════════════════════════════
# 每条标准化记录含: ts, month, title, vid, plat,
# play, like, comment, share, collect, fan_incr, interact

def _key_by_month_title(title, month):
    """生成去重 key：取标题前30字+月份，消除跨平台重复"""
    t = (title or '')[:30].strip()
    return f"{month}::{t}"

def norm_douyin(recs):
    """标准化抖音记录"""
    items = []
    for rec in recs:
        f = rec['fields']
        ts = parse_ts(f.get('发布时间'))
        mk = ts_to_month(ts)
        title = title_str(f.get('作品名称', ''))[:60]
        vid   = str(f.get('视频编号') or '').strip()
        # vid 为空时用「标题+月」做 key
        key   = vid or _key_by_month_title(title, mk)
        play    = int(num(f.get('播放量')))
        like    = int(num(f.get('点赞量')))
        comment = int(num(f.get('评论量')))
        share   = int(num(f.get('分享量')))
        collect = int(num(f.get('收藏量')))
        fan     = int(num(f.get('粉丝增量 (1)')))
        interact = like + comment + share + collect
        items.append({'ts': ts, 'month': mk, 'title': title, 'vid': key,
                       'plat': 'douyin', 'play': play, 'like': like,
                       'comment': comment, 'share': share,
                       'collect': collect, 'fan_incr': fan, 'interact': interact})
    return items

def norm_ks(recs):
    """标准化快手记录"""
    items = []
    for rec in recs:
        f = rec['fields']
        ts = parse_ts(f.get('发布时间'))
        mk = ts_to_month(ts)
        title = title_str(f.get('作品', ''))[:60]
        vid   = str(f.get('视频编号') or '').strip()
        key   = vid or _key_by_month_title(title, mk)
        play    = int(num(f.get('播放量')))
        like    = int(num(f.get('点赞量')))
        comment = int(num(f.get('评论量')))
        share   = 0
        collect = int(num(f.get('收藏量')))
        fan     = int(num(f.get('涨粉量')))
        interact = like + comment + collect
        items.append({'ts': ts, 'month': mk, 'title': title, 'vid': key,
                       'plat': 'ks', 'play': play, 'like': like,
                       'comment': comment, 'share': share,
                       'collect': collect, 'fan_incr': fan, 'interact': interact})
    return items

def norm_bili(recs):
    """标准化B站记录"""
    items = []
    for rec in recs:
        f = rec['fields']
        ts = parse_ts(f.get('发布时间'))
        mk = ts_to_month(ts)
        title = title_str(f.get('视频标题', ''))[:60]
        vid   = str(f.get('视频编号') or '').strip()
        key   = vid or _key_by_month_title(title, mk)
        play    = int(num(f.get('播放量')))
        like    = int(num(f.get('点赞量')))
        comment = int(num(f.get('评论量')))
        share   = int(num(f.get('转发量')))
        collect = int(num(f.get('收藏量')))
        fan     = int(num(f.get('涨粉量')))
        interact = like + comment + share + collect
        items.append({'ts': ts, 'month': mk, 'title': title, 'vid': key,
                       'plat': 'bili', 'play': play, 'like': like,
                       'comment': comment, 'share': share,
                       'collect': collect, 'fan_incr': fan, 'interact': interact})
    return items

def norm_wx(recs):
    """标准化视频号记录"""
    items = []
    for rec in recs:
        f = rec['fields']
        ts = parse_ts(f.get('发布时间'))
        mk = ts_to_month(ts)
        title = title_str(f.get('视频描述', ''))[:60]
        vid   = str(f.get('视频编号') or '').strip()
        key   = vid or _key_by_month_title(title, mk)
        play    = int(num(f.get('播放量')))
        like    = int(num(f.get('喜欢')))
        comment = int(num(f.get('评论量')))
        share   = int(num(f.get('分享量')))
        collect = 0
        fan     = int(num(f.get('关注量')))
        interact = like + comment + share
        items.append({'ts': ts, 'month': mk, 'title': title, 'vid': key,
                       'plat': 'wx', 'play': play, 'like': like,
                       'comment': comment, 'share': share,
                       'collect': collect, 'fan_incr': fan, 'interact': interact})
    return items


# ═══════════════════════════════════════════════════════
# 从四平台分表聚合汇总数据（绕过150条汇总表限制）
# ═══════════════════════════════════════════════════════

def process_summary_from_platforms(dy_recs, ks_recs, bili_recs, wx_recs):
    """从四平台分表聚合全量汇总数据"""
    # 标准化所有记录
    dy_items = norm_douyin(dy_recs)
    ks_items = norm_ks(ks_recs)
    bili_items = norm_bili(bili_recs)
    wx_items = norm_wx(wx_recs)
    all_items = dy_items + ks_items + bili_items + wx_items

    # ── 全局总量 ──────────────────────────────────────
    total_play = total_like = total_comment = total_share = 0
    total_collect = total_fan = 0
    plat_play = {'douyin': 0, 'ks': 0, 'bili': 0, 'wx': 0}
    plat_like = {'douyin': 0, 'ks': 0, 'bili': 0, 'wx': 0}

    for it in all_items:
        total_play     += it['play']
        total_like     += it['like']
        total_comment  += it['comment']
        total_share    += it['share']
        total_collect  += it['collect']
        total_fan      += it['fan_incr']
        plat = it['plat']
        plat_play[plat] += it['play']
        plat_like[plat] += it['like']

    total_interact = total_like + total_comment + total_share + total_collect

    # ── 月度趋势（按平台分拆）────────────────────────────
    monthly = {}  # { month: { plat: {play, interact, count}, ... } }
    for it in all_items:
        mk = it['month']
        if not mk: continue
        if mk not in monthly:
            monthly[mk] = {
                'douyin': {'play': 0, 'interact': 0, 'count': 0},
                'ks':     {'play': 0, 'interact': 0, 'count': 0},
                'bili':   {'play': 0, 'interact': 0, 'count': 0},
                'wx':     {'play': 0, 'interact': 0, 'count': 0},
            }
        monthly[mk][it['plat']]['play']     += it['play']
        monthly[mk][it['plat']]['interact'] += it['interact']
        monthly[mk][it['plat']]['count']    += 1

    sm = sorted(monthly.keys())

    # ── 跨平台去重 Top20（按 vid/title+month）──────────
    # 同一条视频可能出现在多个平台，取播放量最大那条
    best = {}  # { vid_key: record }
    for it in all_items:
        key = it['vid']
        if key not in best or it['play'] > best[key]['play']:
            best[key] = it

    # 排序后取 Top20
    top20 = sorted(best.values(), key=lambda x: x['play'], reverse=True)[:20]
    top20_formatted = []
    for v in top20:
        # 该视频在各平台的数据
        dy_p = dy_p2 = ks_p = bi_p = wx_p = 0
        if v['plat'] == 'douyin': dy_p = v['play']
        elif v['plat'] == 'ks':   ks_p = v['play']
        elif v['plat'] == 'bili': bi_p = v['play']
        elif v['plat'] == 'wx':   wx_p = v['play']
        # 跨平台补充：同标题+月的其他平台数据
        for alt in all_items:
            if alt['vid'] == v['vid'] and alt['plat'] != v['plat']:
                if   alt['plat'] == 'douyin': dy_p = max(dy_p, alt['play'])
                elif alt['plat'] == 'ks':     ks_p = max(ks_p, alt['play'])
                elif alt['plat'] == 'bili':   bi_p = max(bi_p, alt['play'])
                elif alt['plat'] == 'wx':     wx_p = max(wx_p, alt['play'])
        top20_formatted.append({
            'title': v['title'],
            'author': '',
            'date_ts': v['ts'],
            'month': v['month'],
            'plat': v['plat'],
            'douyin_play': dy_p, 'ks_play': ks_p,
            'bili_play':   bi_p, 'wx_play': wx_p,
            'total_play':    dy_p + ks_p + bi_p + wx_p,
            'total_interact': v['interact'],
            'total_comment':  v['comment'],
            'total_share':    v['share'],
        })

    return {
        'total_play':     total_play,
        'total_interact': total_interact,
        'total_comment':  total_comment,
        'total_share':    total_share,
        'total_collect': total_collect,
        'total_fan':     total_fan,
        'total_videos':  len(best),
        'raw_videos':    len(all_items),
        'plat_play': {k: int(v) for k, v in plat_play.items()},
        'plat_like': {k: int(v) for k, v in plat_like.items()},
        'trend_labels':   sm,
        'trend_douyin':   [monthly[m]['douyin']['play']   for m in sm],
        'trend_ks':       [monthly[m]['ks']['play']        for m in sm],
        'trend_bili':     [monthly[m]['bili']['play']      for m in sm],
        'trend_wx':       [monthly[m]['wx']['play']        for m in sm],
        'trend_interact': [sum(monthly[m][p]['interact'] for p in ('douyin','ks','bili','wx')) for m in sm],
        'trend_count':    [sum(monthly[m][p]['count']    for p in ('douyin','ks','bili','wx')) for m in sm],
        'top20':          top20_formatted,
        'updated_at':     datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'record_count':   len(all_items),
    }


def process_summary(records):
    """保留旧接口，兼容旧汇总表（但主流程已迁移到 process_summary_from_platforms）"""
    # 原有逻辑（已修复日期解析）→ 仅当汇总表有数据时使用
    total_play = total_interact = total_comment = total_share = 0
    plat_play = {'douyin': 0, 'ks': 0, 'bili': 0, 'wx': 0}
    plat_like = {'douyin': 0, 'ks': 0, 'bili': 0, 'wx': 0}
    monthly = {}
    video_list = []

    for item in records:
        f = item.get('fields', {})
        dy_play  = num(f.get('抖音播放量'))
        ks_play  = num(f.get('快手播放量'))
        bi_play  = num(f.get('B站播放量'))
        wx_play  = num(f.get('视频号播放量'))
        dy_like  = num(f.get('抖音点赞量') or f.get('抖音点赞数'))
        ks_like  = num(f.get('快手点赞量') or f.get('快手点赞数'))
        bi_like  = num(f.get('B站点赞量')  or f.get('B站点赞数'))
        wx_like  = num(f.get('视频号喜欢量') or f.get('视频号点赞数') or f.get('视频号点赞量'))
        t_play   = num(f.get('总计播放量')) or (dy_play + ks_play + bi_play + wx_play)
        t_inter  = num(f.get('总计互动量'))
        t_com    = num(f.get('总计评论量') or f.get('评论量'))
        t_shr    = num(f.get('总计转发量') or f.get('转发量'))

        total_play     += t_play
        total_interact += t_inter
        total_comment  += t_com
        total_share    += t_shr
        plat_play['douyin'] += dy_play; plat_play['ks'] += ks_play
        plat_play['bili']   += bi_play; plat_play['wx'] += wx_play
        plat_like['douyin'] += dy_like; plat_like['ks'] += ks_like
        plat_like['bili']   += bi_like; plat_like['wx'] += wx_like

        date_val = f.get('日期') or f.get('发布时间') or f.get('时间')
        mk = None
        if date_val:
            if isinstance(date_val, list):
                date_val = date_val[0] if date_val else None
            if isinstance(date_val, (int, float)):
                mk = ts_to_month(int(date_val))
            elif isinstance(date_val, str) and len(date_val) >= 7:
                mk = date_val[:7]

        if mk:
            if mk not in monthly:
                monthly[mk] = {'douyin': 0, 'ks': 0, 'bili': 0, 'wx': 0, 'interact': 0, 'count': 0}
            monthly[mk]['douyin']   += dy_play; monthly[mk]['ks']       += ks_play
            monthly[mk]['bili']     += bi_play; monthly[mk]['wx']       += wx_play
            monthly[mk]['interact'] += t_inter; monthly[mk]['count']    += 1

        t_val = f.get('视频描述') or f.get('标题') or f.get('视频标题') or '（无标题）'
        a_val = f.get('第一作者') or f.get('作者') or ''
        video_list.append({
            'title': title_str(t_val)[:60], 'author': author_str(a_val),
            'date_ts': int(date_val[0]) if isinstance(date_val, list) else int(date_val) if isinstance(date_val, (int, float)) else 0,
            'douyin_play': int(dy_play), 'ks_play': int(ks_play),
            'bili_play': int(bi_play),   'wx_play': int(wx_play),
            'total_play': int(t_play),   'total_interact': int(t_inter),
            'total_comment': int(t_com), 'total_share': int(t_shr),
        })

    sm = sorted(monthly.keys())
    return {
        'total_play':     int(total_play),
        'total_interact': int(total_interact),
        'total_comment':  int(total_comment),
        'total_share':    int(total_share),
        'total_videos':   len(records),
        'plat_play': {k: int(v) for k, v in plat_play.items()},
        'plat_like': {k: int(v) for k, v in plat_like.items()},
        'trend_labels':   sm,
        'trend_douyin':   [int(monthly[m]['douyin'])   for m in sm],
        'trend_ks':       [int(monthly[m]['ks'])       for m in sm],
        'trend_bili':     [int(monthly[m]['bili'])     for m in sm],
        'trend_wx':       [int(monthly[m]['wx'])       for m in sm],
        'trend_interact': [int(monthly[m]['interact']) for m in sm],
        'trend_count':    [int(monthly[m]['count'])    for m in sm],
        'top20':          sorted(video_list, key=lambda x: x['total_play'], reverse=True)[:20],
        'updated_at':     datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'record_count':   len(records),
    }


# ═══════════════════════════════════════════════════════
# 平台独立表处理
# ═══════════════════════════════════════════════════════

def process_douyin(records):
    items = []
    for rec in records:
        f = rec['fields']
        ts = parse_ts(f.get('发布时间'))
        fr_raw = num(f.get('完播率'))
        # 完播率有时是 0-1，有时是百分比，做归一化
        finish_rate = fr_raw if fr_raw <= 1 else fr_raw / 100
        items.append({
            'ts': ts, 'month': ts_to_month(ts),
            'title': title_str(f.get('作品名称',''))[:80],
            'play': int(num(f.get('播放量'))),
            'like': int(num(f.get('点赞量'))),
            'comment': int(num(f.get('评论量'))),
            'share': int(num(f.get('分享量'))),
            'collect': int(num(f.get('收藏量'))),
            'home_visit': int(num(f.get('主页访问量'))),
            'fan_incr': int(num(f.get('粉丝增量 (1)'))),
            'finish_rate': round(finish_rate, 4),
            'finish5s': round(num(f.get('5s完播率')), 4),
            'avg_dur': round(num(f.get('平均播放时长')), 2),
            'vid': str(f.get('视频编号', '')),
        })
    return _agg_platform(items, extras=['collect', 'home_visit', 'fan_incr', 'finish_rate', 'avg_dur'])

def process_ks(records):
    items = []
    for rec in records:
        f = rec['fields']
        ts = parse_ts(f.get('发布时间'))
        fr_s = str(f.get('完播率', '0')).replace('%', '')
        try: fr = float(fr_s) / 100
        except: fr = 0
        items.append({
            'ts': ts, 'month': ts_to_month(ts),
            'title': title_str(f.get('作品',''))[:80],
            'play': int(num(f.get('播放量'))),
            'like': int(num(f.get('点赞量'))),
            'comment': int(num(f.get('评论量'))),
            'collect': int(num(f.get('收藏量'))),
            'fan_incr': int(num(f.get('涨粉量'))),
            'finish_rate': round(fr, 4),
            'vid': str(f.get('视频编号', '')),
        })
    return _agg_platform(items, extras=['collect', 'fan_incr', 'finish_rate'])

def process_bili(records):
    items = []
    for rec in records:
        f = rec['fields']
        ts = parse_ts(f.get('发布时间'))
        ap_s = str(f.get('平均播放进度', '0%')).replace('%', '')
        try: ap = float(ap_s) / 100
        except: ap = 0
        ir_s = str(f.get('互动率', '0')).replace('%', '')
        try: ir = float(ir_s)
        except: ir = 0
        items.append({
            'ts': ts, 'month': ts_to_month(ts),
            'title': title_str(f.get('视频标题',''))[:80],
            'play': int(num(f.get('播放量'))),
            'like': int(num(f.get('点赞量'))),
            'comment': int(num(f.get('评论量'))),
            'coin': int(num(f.get('投币量'))),
            'collect': int(num(f.get('收藏量'))),
            'forward': int(num(f.get('转发量'))),
            'danmu': int(num(f.get('弹幕量'))),
            'fan_incr': int(num(f.get('涨粉量'))),
            'interact_rate': round(ir, 4),
            'avg_progress': round(ap, 4),
            'vid': str(f.get('视频编号', '')),
        })
    return _agg_platform(items, extras=['coin', 'collect', 'forward', 'danmu', 'fan_incr'])

def process_wx(records):
    items = []
    for rec in records:
        f = rec['fields']
        ts = parse_ts(f.get('发布时间'))
        fr_raw = num(f.get('完播率'))
        finish_rate = fr_raw if fr_raw <= 1 else fr_raw / 100
        items.append({
            'ts': ts, 'month': ts_to_month(ts),
            'title': title_str(f.get('视频描述',''))[:80],
            'play': int(num(f.get('播放量'))),
            'like': int(num(f.get('喜欢'))),
            'comment': int(num(f.get('评论量'))),
            'share': int(num(f.get('分享量'))),
            'forward_chat': int(num(f.get('转发聊天和朋友圈'))),
            'fan_incr': int(num(f.get('关注量'))),
            'recommend': int(num(f.get('推荐'))),
            'finish_rate': round(finish_rate, 4),
            'avg_dur': round(num(f.get('平均播放时长')), 2),
            'vid': str(f.get('视频编号', '')),
        })
    return _agg_platform(items, extras=['share', 'forward_chat', 'fan_incr', 'recommend', 'finish_rate', 'avg_dur'])


def _agg_platform(items, extras=None):
    """通用平台聚合：月度趋势 + 总量 + Top20"""
    extras = extras or []
    monthly = {}
    total_play = total_like = total_comment = total_fan = 0
    extra_totals = {k: 0.0 for k in extras if k not in ('finish_rate',)}
    finish_sum = finish_n = 0

    for it in items:
        total_play    += it.get('play', 0)
        total_like    += it.get('like', 0)
        total_comment += it.get('comment', 0)
        total_fan     += it.get('fan_incr', 0)
        for k in extras:
            if k == 'finish_rate':
                fr = it.get('finish_rate', 0)
                if 0 < fr <= 1:
                    finish_sum += fr; finish_n += 1
            elif k in it:
                extra_totals[k] += it[k]

        mk = it.get('month')
        if not mk: continue
        if mk not in monthly:
            monthly[mk] = {'play':0,'like':0,'comment':0,'fan_incr':0,'count':0,
                           'finish_sum':0,'finish_n':0}
            for k in extras:
                if k not in ('finish_rate',): monthly[mk][k] = 0
        monthly[mk]['play']    += it.get('play',0)
        monthly[mk]['like']    += it.get('like',0)
        monthly[mk]['comment'] += it.get('comment',0)
        monthly[mk]['fan_incr']+= it.get('fan_incr',0)
        monthly[mk]['count']   += 1
        fr = it.get('finish_rate', 0)
        if 0 < fr <= 1:
            monthly[mk]['finish_sum'] += fr
            monthly[mk]['finish_n']   += 1
        for k in extras:
            if k not in ('finish_rate',) and k in it:
                monthly[mk][k] += it[k]

    sm = sorted(monthly.keys())

    def mo_val(mk, k):
        v = monthly[mk].get(k, 0)
        return round(v, 4) if isinstance(v, float) else int(v)

    # 月度完播率
    mo_finish = []
    for mk in sm:
        fn = monthly[mk]['finish_n']
        mo_finish.append(round(monthly[mk]['finish_sum']/fn, 4) if fn else 0)

    trend = {
        'labels':   sm,
        'play':     [int(monthly[m]['play'])    for m in sm],
        'like':     [int(monthly[m]['like'])    for m in sm],
        'comment':  [int(monthly[m]['comment']) for m in sm],
        'fan_incr': [int(monthly[m]['fan_incr'])for m in sm],
        'count':    [int(monthly[m]['count'])   for m in sm],
        'finish_rate': mo_finish,
    }
    for k in extras:
        if k not in ('finish_rate',):
            trend[k] = [int(monthly[m].get(k,0)) for m in sm]

    top20 = sorted(items, key=lambda x: x.get('play',0), reverse=True)[:20]

    return {
        'total_play':    int(total_play),
        'total_like':    int(total_like),
        'total_comment': int(total_comment),
        'total_fan':     int(total_fan),
        'avg_finish_rate': round(finish_sum/finish_n, 4) if finish_n else 0,
        'extra_totals':  {k: int(v) for k, v in extra_totals.items()},
        'trend':         trend,
        'top20':         top20,
        'total_records': len(items),
    }


def get_platform_data(force=False):
    """拉取四平台独立数据（带缓存）"""
    now = time.time()
    if not force and _cache_platform['data'] and (now - _cache_platform['ts']) < CACHE_TTL:
        print(f"  [缓存] 返回平台缓存数据（{int(now - _cache_platform['ts'])}秒前）")
        return _cache_platform['data']

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 拉取四平台独立数据...")
    try:
        token = get_tenant_token()
        print("  [OK] token OK")
        dy_recs   = fetch_all_records(token, TABLE_DOUYIN)
        ks_recs   = fetch_all_records(token, TABLE_KS)
        bili_recs = fetch_all_records(token, TABLE_BILI)
        wx_recs   = fetch_all_records(token, TABLE_WX)
        data = {
            'douyin': process_douyin(dy_recs),
            'ks':     process_ks(ks_recs),
            'bili':   process_bili(bili_recs),
            'wx':     process_wx(wx_recs),
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        _cache_platform['data'] = data
        _cache_platform['ts']   = now
        print(f"  [OK] 平台数据处理完成")
        return data
    except Exception as e:
        print(f"  [ERR] 失败: {e}")
        if _cache_platform['data']:
            return _cache_platform['data']
        raise


# ═══════════════════════════════════════════════════════
# 原有汇总数据（保持不变）
# ═══════════════════════════════════════════════════════

def get_dashboard_data(force=False):
    """从四平台分表实时聚合全量汇总数据（绕过150条汇总表限制）"""
    now = time.time()
    if not force and _cache_summary['data'] and (now - _cache_summary['ts']) < CACHE_TTL:
        print(f"  [缓存] 汇总数据（{int(now - _cache_summary['ts'])}秒前）")
        return _cache_summary['data']

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 从四平台分表聚合汇总数据...")
    try:
        token = get_tenant_token()
        dy_recs   = fetch_all_records(token, TABLE_DOUYIN)
        ks_recs   = fetch_all_records(token, TABLE_KS)
        bili_recs = fetch_all_records(token, TABLE_BILI)
        wx_recs   = fetch_all_records(token, TABLE_WX)
        data = process_summary_from_platforms(dy_recs, ks_recs, bili_recs, wx_recs)
        _cache_summary['data'] = data
        _cache_summary['ts']   = now
        print(f"  [OK] 聚合完成，总播放 {data['total_play']:,}，原始记录 {data['raw_videos']} 条，去重后 {data['total_videos']} 条")
        return data
    except Exception as e:
        print(f"  [ERR] 失败: {e}")
        if _cache_summary['data']:
            return _cache_summary['data']
        raise


# ═══════════════════════════════════════════════════════
# HTTP Handler
# ═══════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors(); self.end_headers()

    def do_GET(self):
        p = self.path.split('?')[0]
        if p in ('/', '/index.html'):
            self._serve_file('index.html', 'text/html; charset=utf-8')
        elif p == '/api/dashboard':
            self._json(get_dashboard_data())
        elif p == '/api/refresh':
            self._json(get_dashboard_data(force=True))
        elif p == '/api/platforms':
            self._json(get_platform_data())
        elif p == '/api/platforms/refresh':
            self._json(get_platform_data(force=True))
        elif p == '/api/tasks':
            self._json(get_task_data())
        elif p == '/api/tasks/refresh':
            self._json(get_task_data(force=True))
        elif p == '/api/trending':
            self._json(get_trending_data())
        elif p == '/api/trending/refresh':
            self._json(get_trending_data(force=True))
        else:
            self.send_response(404); self.end_headers()

    def _json(self, data):
        try:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({'error': str(e)}).encode('utf-8')
            self.send_response(500); self._cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers(); self.wfile.write(err)

    def _serve_file(self, filename, content_type):
        import os
        path = os.path.join(os.path.dirname(__file__), filename)
        try:
            with open(path, 'rb') as f: body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, fmt, *args):
        if '/api/' in (args[0] if args else ''):
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] {args[0]}")


# ═══════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 55)
    print("  融媒体数据看板 · 本地服务 v2.0")
    print("=" * 55)
    print(f"  启动中... 端口 {PORT}")
    print(f"  主页:      http://localhost:{PORT}")
    print(f"  汇总API:   http://localhost:{PORT}/api/dashboard")
    print(f"  平台API:   http://localhost:{PORT}/api/platforms")
    print(f"  任务API:   http://localhost:{PORT}/api/tasks")
    print(f"  热搜API:   http://localhost:{PORT}/api/trending")
    print(f"  按 Ctrl+C 停止")
    print("=" * 55)

    def preload():
        try:
            get_dashboard_data(force=True)
            get_platform_data(force=True)
            get_task_data(force=True)
        except Exception as e:
            print(f"  [WARN] 预热失败: {e}")

    threading.Thread(target=preload, daemon=True).start()
    server = HTTPServer(('localhost', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  服务已停止")
