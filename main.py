from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import json
import os
from datetime import datetime, timedelta
import astrbot.api.message_components as Comp
from astrbot.api.message_components import At
import random

# 数据持久化，存 data 目录下
# AstrBot 推荐插件数据存储: data/plugins/data-invitecount/invite_data.json
# data 目录应取 context.data_dir 保证兼容任何主目录
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
INVITE_DATA_FILE = os.path.join(DATA_DIR, 'invite_data.json')

def load_data():
    if os.path.exists(INVITE_DATA_FILE):
        with open(INVITE_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(INVITE_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

def get_global_plugin_data_file(context):
    """
    返回主 data/plugin-data/invitecount.json 路径（与 plugins 并列，支持多环境）
    """
    # 推荐优先用 context.data_dir
    base = getattr(context, 'data_dir', None)
    if base and os.path.isdir(base):
        root = base
    else:
        # 向上递归找 'data' 目录
        now = os.path.abspath(os.path.dirname(__file__))
        while True:
            if os.path.basename(now) == 'data':
                root = now
                break
            parent = os.path.dirname(now)
            if parent == now:
                # 极端情况 fallback
                root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../data'))
                break
            now = parent
    plugin_data_dir = os.path.join(root, 'plugin-data')
    os.makedirs(plugin_data_dir, exist_ok=True)
    return os.path.join(plugin_data_dir, 'invitecount.json')

@register("invite_query", "bvzrays", "群邀请统计插件", "1.0.0")
class InviteQueryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or AstrBotConfig({"only_stat_valid": False, "allow_at_query": True, "show_inviter": True})
        logger.debug(f"[invite-plugin] 配置已注入/初始化: {dict(self.config or {})}")
        self.data_file = get_global_plugin_data_file(self.context)
        self.invite_data = self.load_data()

    async def initialize(self):
        logger.debug(f"[invite] 配置已注入: {dict(self.config or {})}")
        logger.info(f"[invite] 数据文件: {self.data_file}，当前记录数: {len(self.invite_data)}")

    def load_data(self):
        # 数据文件加载
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载邀请数据失败：{e}")
        return {}

    def save(self):
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.invite_data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            logger.error(f"保存邀请数据失败：{e}")

    def get_random_bgimg_path(self):
        """自动查找 plugin-data/invitecount_images/ 下背景，返回一个本地文件或None"""
        folder = os.path.join(os.path.dirname(self.data_file), 'invitecount_images')
        if not os.path.exists(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except Exception:
                return None
        imgs = [f for f in os.listdir(folder) if f.lower().endswith(('.png','.jpg','.jpeg','.webp'))]
        if not imgs:
            return None
        return os.path.join(folder, random.choice(imgs))

    async def try_get_nickname(self, group_id, user_id):
        """优先查昵称，有接口用接口，无则直接ID"""
        try:
            if hasattr(self.context, "get_group_member_info"):
                member = await self.context.get_group_member_info(group_id, user_id)
                name = member.get("nickname") or member.get("card") or str(user_id)
                return name
        except Exception as e:
            logger.debug(f"无法获取昵称: {e}")
        return str(user_id)

    async def sync_all_group_members(self, group_id):
        """同步并更新当前群所有入库QQ的nickname为最新群名片/昵称"""
        if not (group_id and hasattr(self.context, 'get_group_member_list')):
            return
        try:
            result = await self.context.get_group_member_list(group_id)
            updated = 0
            for member in result:
                user_id = str(member.get('user_id'))
                name = member.get('card') or member.get('nickname') or member.get('remark') or user_id
                if user_id in self.invite_data:
                    self.invite_data[user_id]['nickname'] = name
                    updated += 1
            if updated:
                self.save()
        except Exception as e:
            logger.debug(f'[invite-debug] 同步群名片异常: {e}')

    async def safe_get_member_name_by_list(self, event, group_id, user_id):
        name = user_id
        try:
            # 只要 event 有 bot 和 api，就直接用
            if hasattr(event, "bot") and hasattr(event.bot, "api"):
                members = await event.bot.api.call_action('get_group_member_list', group_id=group_id)
                for member in members:
                    if str(member.get("user_id")) == str(user_id):
                        card = member.get("card", "").strip()
                        nickname = member.get("nickname", "").strip()
                        if card:
                            name = card
                        elif nickname:
                            name = nickname
                        break
        except Exception as e:
            logger.debug(f'[invite debug] get_group_member_list失败: {e}')
        return name

    async def try_render_html(self, event, html_body, data, fallback_text):
        """尝试用 AstrBot 图片渲染接口(html_render)输出，支持随机本地背景且卡片全填充，失败则返回文本。"""
        if not self.config.get("enable_image_render", False):
            yield event.plain_result(fallback_text)
            return
        bgimg_path = self.get_random_bgimg_path()
        if bgimg_path:
            safe_img_path = bgimg_path.replace(os.sep, '/')
            # 保证兼容 windows 路径+file://协议
            if not safe_img_path.startswith("file:///"):
                if safe_img_path.startswith("/"):
                    safe_img_path = "file://" + safe_img_path
                else:
                    safe_img_path = "file:///" + safe_img_path
            bgimg_css = (
                f"background-image:url('{safe_img_path}');"
                "background-size:cover;"
                "background-repeat:no-repeat;"
                "background-position:center center;"
                "min-width:410px;max-width:560px;min-height:230px;"
                "box-sizing:border-box;border-radius:20px;"
                "box-shadow:0 4px 32px #42545c44;"
                "overflow:hidden;padding:0;"
            )
        else:
            bgimg_css = (
                "background:linear-gradient(120deg,#fdf6ee 0%,#dbe9fa 100%);"
                "min-width:410px;max-width:560px;min-height:230px;"
                "box-sizing:border-box;border-radius:20px;"
                "box-shadow:0 4px 32px #42545c44;"
                "overflow:hidden;padding:0;"
            )
        html_body = html_body.replace("background:__BG__;", bgimg_css)
        try:
            url = await self.html_render(html_body, data, return_url=True)
            yield event.image_result(url)
        except Exception as e:
            logger.debug(f'[invite debug] 图片渲染失败: {e}')
            yield event.plain_result(fallback_text)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def handle_group_event(self, event: AstrMessageEvent):
        data = getattr(event.message_obj, 'raw_message', {})
        logger.debug(f"[invite debug] 收到群事件: {data}")
        if not isinstance(data, dict):
            logger.debug("[invite debug] 原始事件不是 dict，跳过")
            return

        # 兼容多协议字段名（OneBot v11/v12、Napcat、Lagrange等）
        post_type = data.get('post_type') or data.get('type') or data.get('notice_type')
        raw_notice = data.get('notice_type') or data.get('event') or data.get('detail_type')
        sub_type = data.get('sub_type') or data.get('subEvent') or data.get('extra_type')

        # ID 归一化
        group_id = (
            data.get('group_id')
            or data.get('chat_id')
            or data.get('group')
            or data.get('groupId')
            or ''
        )
        user_id = (
            data.get('user_id')
            or data.get('target_id')
            or data.get('member_id')
            or data.get('userId')
            or data.get('member')
            or ''
        )
        operator_id = (
            data.get('operator_id')
            or data.get('inviter_id')
            or data.get('operator_user_id')
            or data.get('operatorUid')
            or data.get('inviter')
            or ''
        )

        group_id = str(group_id) if group_id is not None else ''
        user_id = str(user_id) if user_id is not None else ''
        operator_id = str(operator_id) if operator_id is not None else ''

        # 统一 notice_type 语义
        notice_type = raw_notice
        # 常见别名归一化
        alias_increase = {"group_increase", "member_increase", "group_member_increase", "group_member_increase_event"}
        alias_decrease = {"group_decrease", "member_decrease", "group_member_decrease", "group_member_decrease_event"}
        if notice_type in alias_increase:
            notice_type = "group_increase"
        elif notice_type in alias_decrease:
            notice_type = "group_decrease"

        # 子类型归一化
        if sub_type in {"join", "approve", "increase", "pass"}:
            sub_type = "approve"  # 主动加群/管理员同意
        elif sub_type in {"invite", "invited"}:
            sub_type = "invite"
        elif sub_type in {"leave", "quit", "exit"}:
            sub_type = "leave"
        elif sub_type in {"kick", "kick_me", "ban"}:
            sub_type = "kick"

        logger.debug(
            f"[invite debug] 归一化: post_type={post_type}, notice_type={notice_type}, sub_type={sub_type}, "
            f"group_id={group_id}, user_id={user_id}, operator_id={operator_id}"
        )

        time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if (post_type == "notice" or post_type == "group_notice") and group_id:
            if notice_type == "group_increase":
                # 新成员进群
                try:
                    member_name = await self.try_get_nickname(group_id, user_id)
                except Exception as e:
                    logger.debug(f"[invite debug] 获取成员名异常: {e}")
                    member_name = user_id
                if sub_type == "invite" and operator_id:
                    try:
                        operator_name = await self.try_get_nickname(group_id, operator_id)
                    except Exception as e:
                        logger.debug(f"[invite debug] 获取邀请人名异常: {e}")
                        operator_name = operator_id
                    self.invite_data.setdefault(str(user_id), {})
                    self.invite_data[str(user_id)] = {
                        "nickname": member_name,
                        "inviter": str(operator_id),
                        "inviter_name": operator_name,
                        "join_type": "邀请",
                        "join_time": time,
                        "leave_type": None,
                        "leave_time": None
                    }
                    logger.info(f"[invite debug] 邀请入群已记: user_id={user_id}, inviter={operator_id}")
                    self.save()
                else:
                    # 无 operator 视为主动或未识别，记为主动
                    self.invite_data.setdefault(str(user_id), {})
                    self.invite_data[str(user_id)] = {
                        "nickname": member_name,
                        "inviter": None,
                        "inviter_name": None,
                        "join_type": "主动",
                        "join_time": time,
                        "leave_type": None,
                        "leave_time": None
                    }
                    logger.info(f"[invite debug] 主动/未知方式入群已记: user_id={user_id}, sub_type={sub_type}")
                    self.save()
            elif notice_type == "group_decrease":
                # 成员退群/被踢
                if sub_type == "leave":
                    if str(user_id) in self.invite_data:
                        self.invite_data[str(user_id)]["leave_type"] = "自己退群"
                        self.invite_data[str(user_id)]["leave_time"] = time
                        logger.info(f"[invite debug] 成员退群: user_id={user_id}")
                    else:
                        logger.debug(f"[invite debug] 退群用户未在记录中: user_id={user_id}")
                    self.save()
                elif sub_type == "kick":
                    if str(user_id) in self.invite_data:
                        self.invite_data[str(user_id)]["leave_type"] = f"被踢({operator_id})"
                        self.invite_data[str(user_id)]["leave_time"] = time
                        logger.info(f"[invite debug] 成员被踢: user_id={user_id}, by {operator_id}")
                    else:
                        logger.debug(f"[invite debug] 被踢用户未在记录中: user_id={user_id}")
                    self.save()
                else:
                    logger.debug(f"[invite debug] 未识别的减少子类型: sub_type={sub_type}")
        else:
            logger.debug(
                f"[invite debug] 非 notice 或缺少 group_id，post_type={post_type}, group_id={group_id}"
            )

    # ==== 查询统计命令 ====
    @filter.command("邀请查询")
    async def cmd_invite_query(self, event: AstrMessageEvent, qq: str = ""):
        user_id = None
        group_id = None
        if hasattr(event, 'get_group_id'):
            group_id = getattr(event, 'get_group_id', lambda: None)() or None
        if not group_id:
            raw = getattr(event.message_obj, 'raw_message', {})
            group_id = str(raw.get('group_id', None)) if raw else None
        await self.sync_all_group_members(group_id)
        from astrbot.api.message_components import At
        for seg in getattr(event.message_obj, "message", []):
            if isinstance(seg, At):
                user_id = str(seg.qq)
                break
            if getattr(seg, 'type', None) == 'at':
                data = getattr(seg, 'data', {})
                user_id = str(data.get('qq', '')) or str(getattr(seg, 'qq', ''))
                break
        if not user_id:
            arg_qq = qq.strip()
            if not arg_qq:
                args = (event.message_str or '').strip().split()
                if len(args) >= 2 and args[1].isdigit():
                    arg_qq = args[1]
            if arg_qq and arg_qq.isdigit():
                user_id = arg_qq
            else:
                user_id = event.get_sender_id()
        member = self.invite_data.get(str(user_id))
        created = False
        name = await self.safe_get_member_name_by_list(event, group_id, user_id)
        # fallback如有必要
        if not name or name == user_id:
            def getf(v):
                return v.strip() if isinstance(v,str) else v
            if group_id and hasattr(self.context, "get_group_member_info"):
                try:
                    result = await self.context.get_group_member_info(str(group_id), str(user_id))
                    logger.debug(f'[invite debug] 单独接口查返回: {result}')
                    mi = result.get('data', result)
                    card = getf(mi.get('card', ''))
                    nickname = getf(mi.get('nickname', ''))
                    remark = getf(mi.get('remark', ''))
                    displayname = getf(mi.get('displayname', ''))
                    username = getf(mi.get('user_name', ''))
                    name = card or nickname or remark or displayname or username or user_id
                except Exception as e:
                    logger.debug(f'[invite debug] get_group_member_info异常: {e}')
        if not member:
            member = {
                "nickname": name if name else user_id,
                "inviter": None,
                "inviter_name": None,
                "join_type": None,
                "join_time": None,
                "leave_type": None,
                "leave_time": None
            }
            self.invite_data[str(user_id)] = member
            self.save()
            created = True
        # 始终刷新本地nickname缓存
        self.invite_data[str(user_id)]["nickname"] = name if name else user_id
        self.save()
        inviter = member.get("inviter")
        inviter_name = None
        if self.config.get("show_inviter", True) and inviter:
            inviter_name = await self.safe_get_member_name_by_list(event, group_id, inviter)
        inviter_display = "自己/主动进群"
        if inviter and inviter_name:
            inviter_display = f"{inviter_name} ({inviter})"
        elif inviter:
            inviter_display = f"{inviter}"
        join_type = member.get("join_type") or "-"
        join_time = member.get("join_time") or "-"
        leave_type = member.get("leave_type")
        leave_time = member.get("leave_time")
        now = datetime.now()
        days_ago = "-"
        if join_time:
            try:
                join_dt = datetime.strptime(join_time, '%Y-%m-%d %H:%M:%S')
                days_ago = f"{(now - join_dt).days}天前({join_dt.strftime('%Y-%m-%d')})"
            except Exception:
                days_ago = join_time
        all_invited = [(u, v) for u, v in self.invite_data.items() if v.get("inviter") == str(user_id)]
        if self.config.get("only_stat_valid", False):
            invited = [item for item in all_invited if not item[1].get("leave_type")]
        else:
            invited = all_invited
        total_invite = len(all_invited)
        # 修正被踢人数和自己退群人数统计
        kicked = 0
        leave = 0
        for _, v in all_invited:
            lt = v.get("leave_type") or ""
            if lt.startswith("被踢"):
                kicked += 1
            elif lt == "自己退群":
                leave += 1
        valid_invite = len([item for item in all_invited if not item[1].get("leave_type")])
        logger.debug(
            f"[invite debug] 统计: user_id={user_id}, total={total_invite}, valid={valid_invite}, "
            f"kicked={kicked}, leave={leave}, only_stat_valid={self.config.get('only_stat_valid', False)}"
        )
        msg = f"====邀请统计====\n\n"
        msg += f"●被查用户：{name}\n"
        msg += f"●用户QQ：{user_id}\n"
        msg += f"●邀请人：{inviter_display}\n"
        msg += f"●进群方式：{join_type}\n"
        msg += f"●进群时间：{days_ago}\n"
        msg += f"●累计邀请：{total_invite} 人\n"
        msg += f"●被踢人数：{kicked} 人\n"
        msg += f"●自己退群：{leave} 人\n"
        msg += f"●有效邀请：{valid_invite} 人\n"
        msg += f"=================\n\n"
        msg += datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        if created:
            msg = "【提示】该用户暂无数据，已帮你新建统计模板！\n" + msg
        # 新美观卡片布局
        html_body = f"""
<div style='background:__BG__;'>
  <div style='background:rgba(255,255,255,0.82);backdrop-filter: blur(7.2px);margin:18px 18px 14px 18px;padding:22px 22px 10px 20px;border-radius:15px;box-shadow:0 1.5px 8.5px #58ace266;'>
    <div style='display:flex;align-items:flex-end;justify-content:space-between;'>
      <div style='font-weight:800;font-size:1.47rem;color:#395db6;text-shadow:0 2px 10px #f4f8ff;margin-bottom:2px;letter-spacing:2px'>邀请统计</div>
      <div style='font-size:0.97rem;color:#888'>{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}</div>
    </div>
    <hr style='border:none;border-top:1.3px solid #dbe7fe;margin:7.5px 0 13px 0'>
    <table style='width:100%;font-size:1.01rem;line-height:2.18em;color:#333;'>
        <tr><td style='color:#888;width:75px'>被查用户</td><td style='font-weight:bold'>{name}</td></tr>
        <tr><td style='color:#888;'>用户QQ</td><td>{user_id}</td></tr>
        <tr><td style='color:#888;'>邀请人</td><td>{inviter_display}</td></tr>
        <tr><td style='color:#888;'>进群方式</td><td>{join_type}</td></tr>
        <tr><td style='color:#888;'>进群时间</td><td>{days_ago}</td></tr>
        <tr><td style='color:#888;'>累计邀请</td><td>{total_invite} 人</td></tr>
        <tr><td style='color:#888;'>被踢人数</td><td>{kicked} 人</td></tr>
        <tr><td style='color:#888;'>自己退群</td><td>{leave} 人</td></tr>
        <tr><td style='color:#de5d62;'>有效邀请</td><td><b>{valid_invite} 人</b></td></tr>
    </table>
  </div>
</div>
"""
        async for result in self.try_render_html(event, html_body, {}, msg):
            yield result

    @filter.command("我的邀请")
    async def cmd_my_invite(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        event.message_str = f"/邀请查询 @{user_id}"  # 伪造at查询自己
        async for r in self.cmd_invite_query(event):
            yield r

    @filter.command("邀请排行")
    async def cmd_invite_rank(self, event: AstrMessageEvent, mode: str = ""):
        """排行模式说明:
        /邀请排行         # 有效邀请排行（全量）
        /邀请排行 总      # 总邀请排行
        /邀请排行 差      # 无效邀请排行
        /邀请排行 周      # 最近7天新邀请有效人数排行
        /邀请排行 月      # 最近30天新邀请有效人数排行
        /邀请排行 帮助    # 帮助
        """
        text = "====邀请排行====\n"
        args = (event.message_str or '').strip().split()
        if len(args) >= 2:
            mode = args[1].strip()
        if mode in {"help", "帮助", "h", "?"}:
            text += (
                "/邀请排行              —— 全量有效邀请排行\n"
                "/邀请排行 总（或 总人数）—— 总邀请排行\n"
                "/邀请排行 差（或 无效人数）—— 无效邀请排行\n"
                "/邀请排行 周            —— 最近7天邀请排行\n"
                "/邀请排行 月            —— 最近30天邀请排行\n"
                "/邀请排行 帮助         —— 显示本帮助\n"
            )
            yield event.plain_result(text)
            return
        # 统计范围选择
        now = datetime.now()
        cutoff = None
        period_display = "全量"
        if mode in {"周","week"}:
            cutoff = now - timedelta(days=7)
            period_display = "最近7天"
        elif mode in {"月","month"}:
            cutoff = now - timedelta(days=30)
            period_display = "最近30天"
        # 汇总数据生成
        count_map = {}  # inviter: [有效, 总, 无效]
        inviter_name_map = {}
        for v in self.invite_data.values():
            inviter = v.get("inviter")
            join_time_str = v.get("join_time")
            # 判断是否在时间窗口内
            in_time = True
            if cutoff and join_time_str:
                try:
                    join_dt = datetime.strptime(join_time_str, '%Y-%m-%d %H:%M:%S')
                    in_time = join_dt >= cutoff
                except Exception:
                    in_time = False
            if not inviter or (cutoff and not in_time):
                continue
            is_invalid = (v.get("leave_type") is not None)
            if inviter not in count_map:
                count_map[inviter] = [0, 0, 0]  # 有效, 总, 无效
            count_map[inviter][1] += 1  # 总
            if not is_invalid:
                count_map[inviter][0] += 1  # 有效
            else:
                count_map[inviter][2] += 1  # 无效
            if inviter not in inviter_name_map or not inviter_name_map[inviter]:
                inviter_name_map[inviter] = self.invite_data.get(inviter, {}).get("nickname", inviter)
        # 排序模式
        display_mode = "有效邀请"
        if mode in {"总", "全部", "all", "人数", "总人数"}:
            sort_key = 1  # 总
            display_mode = "总邀请"
        elif mode in {"差", "失效", "无效", "无效人数"}:
            sort_key = 2  # 无效
            display_mode = "无效邀请"
        else:
            sort_key = 0  # 有效
            display_mode = f"{period_display}{display_mode}"
        sorted_list = sorted(count_map.items(), key=lambda x: -x[1][sort_key])
        text += f"({display_mode}排行，前10)\n"
        for idx, (uid, tpl) in enumerate(sorted_list[:10], 1):
            name = inviter_name_map.get(uid, uid)
            text += (
                f"{idx}. {name}({uid}) | 有效:{tpl[0]} 总:{tpl[1]} 无效:{tpl[2]}\n"
            )
        if not sorted_list:
            text += "无邀请记录\n"
        # HTML榜模板
        rows_html = "".join(
            f"<tr>"
            f"<td style='font-weight:bold;font-size:1.08em;color:{'#f5ad2e' if idx==1 else ('#bebebe' if idx==2 else ('#e3925d' if idx==3 else '#30b88d'))};'>{idx}.</td>"
            f"<td style='font-weight:bold;color:#204891'>{inviter_name_map.get(uid,uid)}</td>"
            f"<td style='color:#988;font-size:0.92em'>({uid})</td>"
            f"<td style='padding-left:10px;color:#319c5b'>有效:{tpl[0]}</td>"
            f"<td style='color:#356bb6'>总:{tpl[1]}</td>"
            f"<td style='color:#b85d36'>无效:{tpl[2]}</td>"
            f"</tr>"
            for idx, (uid, tpl) in enumerate(sorted_list[:10], 1)
        )
        html_body = f"""
<div style='background:__BG__;'>
  <div style='background:rgba(255,255,255,0.80);backdrop-filter: blur(6.6px);margin:14px 17px 17px 17px;padding:17px 18px 16px 17px;border-radius:15px;'>
    <div style='font-weight:800;font-size:1.32rem;color:#30b88d;letter-spacing:1.5px;margin-bottom:2.7px;text-shadow:0 2px 12px #eaffeeab;'>🎉 邀请排行榜 TOP10</div>
    <hr style='border:none;border-top:1.1px solid #c6efe2;margin:6.5px 0 12px 0'>
    <table style='width:100%;font-size:1.05rem;line-height:1.9em;'>
    {rows_html if rows_html else "<tr><td colspan='6' style='color:#bbb'>暂无邀请记录</td></tr>"}
    </table>
    <div style='color:#999;font-size:0.91rem;text-align:right;margin-top:7.5px;'>
        {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}</div>
  </div>
</div>
"""
        async for result in self.try_render_html(event, html_body, {}, text):
            yield result

    @filter.command("邀请奖励")
    async def cmd_invite_reward(self, event: AstrMessageEvent):
        msg = self.config.get("reward_message", "暂无奖励内容\n请联系管理员在WebUI配置奖励说明")
        # 提前处理html内容中的换行
        msg_html = msg.replace("\n", "<br>")
        html_body = f"""
<div style='background:__BG__;'>
  <div style='background:rgba(255,255,255,0.92);backdrop-filter: blur(5.5px);margin:17px 23px 18px 18px;padding:16px 17px 15px 19px;border-radius:14px;'>
    <div style='text-align:center;font-weight:bold;color:#f49a1e;font-size:1.38rem;letter-spacing:2px;text-shadow:0 3px 22px #ffe6bf91;'>🎁邀请奖励</div>
    <hr style='border:none;border-top:1px solid #fae5be;margin:9.5px 0 13px 0'>
    <div style='font-size:1.07rem;color:#8d5a19;padding:6px 3px 10px 3px;'>{msg_html}</div>
    <div style='text-align:right;font-size:.91rem;color:#b7ab7d;margin-top:13px;'>奖励内容由WebUI配置</div>
  </div>
</div>
"""
        async for result in self.try_render_html(event, html_body, {}, msg):
            yield result

    async def terminate(self):
        # 卸载插件时可扩展资源释放逻辑
        pass
