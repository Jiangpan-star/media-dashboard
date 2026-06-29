/**
 * Vercel Serverless Function — 智谱 AI 代理
 * 让静态部署的看板页面可以直接使用 AI 对话，无需本地运行后端
 *
 * 部署方式：
 *   1. 将项目推送到 GitHub
 *   2. 在 Vercel 中导入该仓库
 *   3. 设置 Root Directory 为项目根目录
 *   4. Deploy 即可
 *
 * 函数 URL 格式：https://your-app.vercel.app/api/ai-chat
 */

const crypto = require('crypto');

// ═══════════════════════════════════════════════
// 配置
// ═══════════════════════════════════════════════
const ZHIPU_API_KEY = '17b95da1f9ab4101875647c268906faf.C9YLzha2qQ045FSA';
const ZHIPU_API_URL = 'https://open.bigmodel.cn/api/paas/v4/chat/completions';

const SYSTEM_PROMPT = `你是融媒体数据看板的AI智能助手，服务于各类校园/机构融媒体团队。

【你的两大能力】
A. 数据查询与分析：用户问"播放量最高""涨粉最多""最近有什么任务""哪个平台数据最好"等，必须基于[实时数据上下文]回答，给出准确数字、标题、日期。
B. 选题策划建议：用户问"推荐选题""怎么拍""结合热点"等，参考[内容风格库]和[当前热搜]给出可执行的选题方案。

【回答规则】
1. 数据查询类问题：直接引用上下文中的具体数据回答，不要编造数字。如果上下文里没有，老实说"暂无数据"。
2. 选题推荐类问题：一次最多给3个选题，每个包含①爆款标题 ②拍摄思路(2-3句) ③剪辑/创意风格 ④参考对标
3. 必须紧贴用户所在机构的校园/组织生活，结合本地地域特色
4. 严禁涉及：政治、外交、军事、灾难、宗教冲突、民族争议、负面舆情
5. 选题必须具备"可执行性"——团队3天内能拍出来的内容
6. 回复要简洁、口语化、有"人味"，像学姐在跟学弟学妹聊天
7. 涉及节日/节气内容时，结合[当前时间]判断时效性

【内容风格库（参考）】
1. 校园风景美拍系列：春/夏/秋/冬日校园、鸟瞰校园、像素校园、用XX打开校园、色轮/彩带/赛博校园
2. 节日/节点系列：高考倒计时、考研倒计时、毕业祝福、母亲节、父亲节、五四青年节、植树节、记者节
3. 活动记录系列：运动会、迎新、开学典礼、晚会、学术会议、双选会
4. 美食/探索：舌尖上的校园、寻味系列、看展地图
5. 人文故事：校园人物推文、致敬劳动者、师者系列、寻找"闪闪发光"的人
6. 创意拍摄：一镜到底、AI建筑生长、积木/纸张/镜像校园、不同字体打开校训
7. 招生季：美丽校园(鸟瞰/鸟啼/风景混剪)、寻味校园
`;

// ═══════════════════════════════════════════════
// JWT HS256 生成（纯 Node.js crypto，零依赖）
// ═══════════════════════════════════════════════
function genJWT() {
  try {
    const parts = ZHIPU_API_KEY.split('.');
    if (parts.length !== 2) return null;
    const [apiId, apiSecret] = parts;

    const header = { alg: 'HS256', sign_type: 'SIGN' };
    const now = Math.floor(Date.now() / 1000);
    const payload = {
      api_key: apiId,
      exp: now + 3600,
      timestamp: now,
    };

    function b64(obj) {
      return Buffer.from(JSON.stringify(obj))
        .toString('base64url')
        .replace(/=+$/, '');
    }

    const h = b64(header);
    const p = b64(payload);
    const sig = crypto
      .createHmac('sha256', apiSecret)
      .update(`${h}.${p}`)
      .digest('base64url')
      .replace(/=+$/, '');

    return `${h}.${p}.${sig}`;
  } catch (e) {
    console.error('[JWT] gen failed:', e.message);
    return null;
  }
}

// ═══════════════════════════════════════════════
// 调用智谱 API
// ═══════════════════════════════════════════════
async function callZhipu(userMessage, history) {
  const token = genJWT();
  if (!token) {
    return { ok: false, msg: 'API Key 格式错误' };
  }

  const messages = [{ role: 'system', content: SYSTEM_PROMPT }];
  if (Array.isArray(history)) {
    for (const h of history.slice(-10)) {
      if (h.role && h.content) {
        messages.push({ role: h.role, content: h.content });
      }
    }
  }
  messages.push({ role: 'user', content: userMessage });

  const payload = {
    model: 'glm-4-flash',
    messages,
    temperature: 0.8,
    max_tokens: 1200,
    top_p: 0.9,
  };

  const resp = await fetch(ZHIPU_API_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(25000),
  });

  const result = await resp.json();

  if (result.choices && result.choices[0]) {
    const content = result.choices[0].message?.content || '';
    return { ok: true, reply: content, usage: result.usage || {} };
  }
  return { ok: false, msg: `API 返回异常: ${JSON.stringify(result).slice(0, 200)}` };
}

// ═══════════════════════════════════════════════
// Vercel Handler
// ═══════════════════════════════════════════════
module.exports = async function handler(req, res) {
  // CORS 预检
  if (req.method === 'OPTIONS') {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    res.setHeader('Access-Control-Max-Age', '86400');
    return res.status(204).end();
  }

  // CORS 头
  res.setHeader('Access-Control-Allow-Origin', '*');

  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, msg: '仅支持 POST' });
  }

  try {
    const { message, history } = req.body || {};

    if (!message || !message.trim()) {
      return res.status(400).json({ ok: false, msg: '消息不能为空' });
    }

    // 注入时间上下文
    const now = new Date();
    const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
    const timeCtx = `[当前时间] ${now.getFullYear()}年${now.getMonth() + 1}月${now.getDate()}日 ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}（周${weekdays[now.getDay()]}）`;
    const fullMsg = `${timeCtx}\n\n[用户问题] ${message.trim()}`;

    const result = await callZhipu(fullMsg, history);
    return res.status(200).json(result);
  } catch (e) {
    console.error('[AI] error:', e.message);
    return res.status(500).json({ ok: false, msg: `AI 服务异常: ${e.message}` });
  }
};
