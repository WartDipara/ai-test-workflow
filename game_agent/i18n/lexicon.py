from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Literal

Locale = Literal["sc", "tc", "en"]

class Concept(str, Enum):
    LOGIN = "login"
    SERVER_SELECT = "server_select"
    SUB_ACCOUNT = "sub_account"
    RESOURCE_DOWNLOAD = "resource_download"
    PRIVACY = "privacy"
    ANNOUNCEMENT = "announcement"
    CHARACTER_CREATION = "character_creation"
    IN_GAME_HUD = "in_game_hud"
    ENTER_GAME = "enter_game"
    START_GAME = "start_game"
    CONFIRM = "confirm"
    DISMISS_CLOSE = "dismiss_close"
    SKIP = "skip"
    CONTINUE = "continue"
    AGREE = "agree"
    CANCEL = "cancel"
    ACCOUNT_LABEL = "account_label"
    PASSWORD_LABEL = "password_label"
    LOGIN_BUTTON = "login_button"
    NETWORK_ERROR = "network_error"
    DOWNLOAD_FAILED = "download_failed"
    SERVER_NOT_EXIST = "server_not_exist"
    CONNECTION_TIMEOUT = "connection_timeout"
    CONNECTION_FAILED = "connection_failed"
    SERVER_BUSY = "server_busy"
    REGION_RESTRICTED = "region_restricted"
    INSTALL = "install"
    INSTALL_ANYWAY = "install_anyway"
    CONTINUE_INSTALL = "continue_install"
    INSTALL_DETAILS = "install_details"
    TUTORIAL = "tutorial"
    LOADING = "loading"
    ENTER_WORLD = "enter_world"
    CHAR_SLOT = "char_slot"
    OVERLAY = "overlay"
    DAILY_NOTICE = "daily_notice"
    HEALTH_ADVISORY = "health_advisory"
    EXCLUDE_AUTH_CONTEXT = "exclude_auth_context"
    SERVER_HINT = "server_hint"
    SERVER_MODAL_TITLE = "server_modal_title"
    SERVER_MODAL_CATEGORY = "server_modal_category"
    PRIVACY_DISAGREE = "privacy_disagree"
    PRIVACY_MODAL_CONSENT = "privacy_modal_consent"
    PRIVACY_TERMS = "privacy_terms"
    COMPOUND_LOGIN = "compound_login"
    FORGOT_PASSWORD = "forgot_password"
    SUB_ACCOUNT_CREATE = "sub_account_create"
    DOWNLOAD_STRONG = "download_strong"
    DOWNLOAD_UPDATING = "download_updating"
    PK_AGREEMENT = "pk_agreement"
    BARE_ENTER = "bare_enter"
    TECHNIQUE = "technique"
    SELECTION = "selection"
    NARRATIVE_CJK = "narrative_cjk"
    EMAIL_VALUE = "email_value"
    PASSWORD_HINT = "password_hint"
    SPATIAL_BUTTON = "spatial_button"
    TUTORIAL_TAP_CARD = "tutorial_tap_card"
    TUTORIAL_DEPLOY = "tutorial_deploy"
    TUTORIAL_TAP_GLOW = "tutorial_tap_glow"


@dataclass(frozen=True, slots=True)
class PhraseSet:
    sc: tuple[str, ...] = ()
    tc: tuple[str, ...] = ()
    en: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()

    def all_locales(self) -> tuple[str, ...]:
        out: list[str] = []
        seen: set[str] = set()
        for phrase in (*self.sc, *self.tc, *self.en, *self.variants):
            if phrase and phrase not in seen:
                seen.add(phrase)
                out.append(phrase)
        return tuple(out)


def _ps(
    *,
    sc: tuple[str, ...] = (),
    tc: tuple[str, ...] = (),
    en: tuple[str, ...] = (),
    variants: tuple[str, ...] = (),
) -> PhraseSet:
    return PhraseSet(sc=sc, tc=tc, en=en, variants=variants)


PHRASES: dict[Concept, PhraseSet] = {
    Concept.CHARACTER_CREATION: _ps(
        sc=(
            "创建角色", "新建角色", "角色创建", "选择角色", "选择职业", "取名",
            "输入名字", "输入名称", "捏脸", "外观设定", "性别选择", "开始冒险",
            "进入游戏", "选择服务器", "下一步", "重新捏脸", "自定义外观",
        ),
        tc=(
            "創建角色", "角色創建", "選擇角色", "選擇職業", "輸入名字", "輸入名稱",
            "捏臉", "外觀設定", "性別選擇", "開始冒險", "進入遊戲", "選擇伺服器",
            "重新捏臉", "自定義外觀",
        ),
        en=(
            "Create Character", "CreateCharacter", "create character",
            "Character Creation", "CharacterCreation", "New Character",
            "Select Class", "Choose Class", "Name your character", "Enter Name",
            "Character Name", "Choose Character", "Select Character", "Customize",
            "Appearance", "Select Gender", "Choose Gender", "Male", "Female",
            "Start Adventure", "Enter Game", "Start Game", "Select Server",
            "Choose Server", "Next", "Next Step",
        ),
    ),
    Concept.IN_GAME_HUD: _ps(
        sc=(
            "商城", "背包", "技能", "任务", "地图", "小地图", "组队", "队伍",
            "邮件", "设置", "系统", "好友", "公会", "帮派", "装备", "属性",
            "角色", "成就", "活动", "日常", "商店", "锻造", "强化", "福利",
            "排行榜", "充值",
        ),
        tc=(
            "任務", "地圖", "小地圖", "組隊", "郵件", "設置", "系統", "公會",
            "幫派", "裝備", "屬性", "活動", "鍛造", "強化", "儲值",
        ),
        en=(
            "Inventory", "Backpack", "Bag", "Skill", "Skills", "Quest", "Quests",
            "Mission", "Missions", "Map", "Minimap", "Team", "Party", "Mail",
            "Settings", "Options", "System", "Friends", "Friend",
            "Guild", "Clan", "Equipment", "Equip", "Gear", "Shop", "Store",
            "Mall", "Market", "Role", "Character", "Status", "Stats", "Bonuses",
            "Bonus", "Event", "Events", "Daily", "Forging", "Forge", "Craft",
            "Crafting", "Enhance", "Upgrade", "Rank", "Ranking", "Leaderboard",
            "Top Up", "Recharge",
        ),
    ),
    Concept.LOGIN: _ps(
        sc=("登录", "立即登录", "注册", "账号", "用户名", "密码", "手机号", "手机验证码"),
        tc=("登入", "登錄", "註冊", "帳號", "用戶名", "密碼", "手機號", "手機驗證碼"),
        en=(
            "login", "log in", "sign in", "sign up", "signup", "register",
            "account", "password", "credential", "login password", "phone number",
            "email", "cell phone",
        ),
    ),
    Concept.LOGIN_BUTTON: _ps(
        sc=("登录", "立即登录"),
        tc=("登入", "立即登入", "登錄"),
        en=("login", "log in", "sign in"),
    ),
    Concept.ACCOUNT_LABEL: _ps(
        sc=("账号", "用户名", "邮箱", "手机", "手机号"),
        tc=("帳號", "用戶名", "郵箱", "手機", "手機號"),
        en=("account", "email", "cell phone", "phone number"),
    ),
    Concept.PASSWORD_LABEL: _ps(
        sc=("密码",),
        tc=("密碼",),
        en=("password",),
    ),
    Concept.PASSWORD_HINT: _ps(
        sc=("输入密码", "请输入密码", "登陆密码", "登录密码"),
        tc=("輸入密碼", "請輸入密碼", "登入密碼"),
        en=("enter password", "please password", "login password"),
    ),
    Concept.COMPOUND_LOGIN: _ps(
        sc=("注册", "忘记", "第三方", "手机验证码", "微博"),
        tc=("註冊", "忘記", "第三方", "手機驗證碼", "微博"),
        en=(
            "sign up", "signup", "register", "forgot", "google", "facebook",
            "apple", "wechat", "qq", "login/sign",
        ),
        variants=("/", "\\", "with"),
    ),
    Concept.FORGOT_PASSWORD: _ps(
        sc=("忘记密码",),
        tc=("忘記密碼",),
        en=("forgot password", "forgot"),
    ),
    Concept.SERVER_SELECT: _ps(
        sc=("选服", "区服", "服务器", "重新选服", "请重新选服", "所选服", "默认服", "线路", "删档", "内测"),
        tc=("選服", "區服", "伺服器", "重新選服", "請重新選服", "所選服", "預設服", "線路", "刪檔", "內測"),
        en=(
            "server select", "select server", "click to select", "server does not exist",
            "re-select server", "reselect server", "realm", "zone", "exclusive",
            "sponsored", "role name",
        ),
        variants=("双认服",),  # OCR misread of 所选服
    ),
    Concept.SERVER_NOT_EXIST: _ps(
        sc=(
            "默认服不存在", "所选服不存在", "选服不存在", "服务器不存在",
            "区服不存在", "请重新选服", "重新选服",
        ),
        tc=(
            "預設服不存在", "所選服不存在", "選服不存在", "伺服器不存在",
            "區服不存在", "請重新選服", "重新選服",
        ),
        en=(
            "server does not exist", "server not exist", "re-select server",
            "failed to fetch server", "failed to load server",
        ),
        variants=("双认服不存在",),
    ),
    Concept.SERVER_HINT: _ps(
        sc=("选服", "区服", "服务器", "线路", "删档", "内测"),
        tc=("選服", "區服", "伺服器", "線路", "刪檔", "內測"),
        en=("server", "click to select", "select server", "realm", "zone", "exclusive", "sponsored", "role name"),
    ),
    Concept.SERVER_MODAL_TITLE: _ps(
        sc=("选择服务器", "选择区服", "服务器列表", "区服列表", "切换服务器"),
        tc=("選擇伺服器", "選擇區服", "伺服器列表", "區服列表", "切換伺服器"),
        en=("select server", "server list"),
    ),
    Concept.SERVER_MODAL_CATEGORY: _ps(
        sc=("推荐", "已有角色", "最新服", "我的角色", "最近登录", "爆满", "火爆", "流畅", "维护"),
        tc=("推薦", "已有角色", "最新服", "我的角色", "最近登入", "爆滿", "火爆", "流暢", "維護"),
        en=("recommended", "maintenance", "smooth", "crowded"),
    ),
    Concept.SUB_ACCOUNT: _ps(
        sc=("小号", "子账号", "选择小号", "选择账号", "选择角色", "上次登录", "默认"),
        tc=("小號", "子帳號", "選擇小號", "選擇帳號", "選擇角色", "上次登入", "預設"),
        en=("sub-account", "subaccount", "last login", "default"),
    ),
    Concept.SUB_ACCOUNT_CREATE: _ps(
        sc=("创建小号", "购买小号"),
        tc=("創建小號", "購買小號"),
        en=("create sub-account", "purchase sub-account"),
    ),
    Concept.RESOURCE_DOWNLOAD: _ps(
        sc=("下载", "资源更新", "资源包", "热更", "加载中", "正在更新", "更新资源", "更新中", "资源更新中", "下载中"),
        tc=("下載", "資源更新", "資源包", "熱更", "載入中", "正在更新", "更新資源", "更新中", "資源更新中", "下載中"),
        en=("download", "downloading", "resource", "patch", "updating", "loading"),
    ),
    Concept.DOWNLOAD_STRONG: _ps(
        sc=("下载", "资源包", "资源更新", "热更"),
        tc=("下載", "資源包", "資源更新", "熱更"),
        en=("download", "resource", "patch"),
    ),
    Concept.DOWNLOAD_UPDATING: _ps(
        sc=("正在更新", "更新资源", "更新中", "资源更新中", "热更", "下载中"),
        tc=("正在更新", "更新資源", "更新中", "資源更新中", "熱更", "下載中"),
        en=("updating", "downloading"),
    ),
    Concept.DOWNLOAD_FAILED: _ps(
        sc=("资源下载失败", "下载失败", "资源加载失败", "更新失败"),
        tc=("資源下載失敗", "下載失敗", "資源載入失敗", "更新失敗"),
        en=("download failed", "update failed", "resource download failed"),
    ),
    Concept.NETWORK_ERROR: _ps(
        sc=(
            "网络连接失败", "网络异常", "网络无连接", "没有网络", "请检查网络",
            "连接超时", "连接失败", "服务器连接失败", "与服务器断开连接",
            "服务器加载失败", "服务器获取失败", "服务器繁忙", "服务器维护中",
            "无法连接服务器", "连接服务器失败", "获取服务器列表失败",
        ),
        tc=(
            "網路連接失敗", "網路異常", "網路無連接", "沒有網路", "請檢查網路",
            "連接超時", "連接失敗", "伺服器連接失敗", "與伺服器斷開連接",
            "伺服器載入失敗", "伺服器獲取失敗", "伺服器繁忙", "伺服器維護中",
            "無法連接伺服器", "連接伺服器失敗", "獲取伺服器列表失敗",
        ),
        en=(
            "network failed", "no network", "connection timeout", "connection failed",
            "server connection", "server busy", "server maintenance",
        ),
    ),
    Concept.CONNECTION_TIMEOUT: _ps(
        sc=("连接超时",),
        tc=("連接超時",),
        en=("connection timeout", "timeout"),
    ),
    Concept.CONNECTION_FAILED: _ps(
        sc=("连接失败", "无法连接服务器", "连接服务器失败"),
        tc=("連接失敗", "無法連接伺服器", "連接伺服器失敗"),
        en=("connection failed", "failed to connect"),
    ),
    Concept.SERVER_BUSY: _ps(
        sc=("服务器繁忙", "服务器维护中"),
        tc=("伺服器繁忙", "伺服器維護中"),
        en=("server busy", "server maintenance"),
    ),
    Concept.REGION_RESTRICTED: _ps(
        sc=("当前地区不支持", "当前区域暂未开放"),
        tc=("當前地區不支持", "當前區域暫未開放"),
        en=("region not supported", "not available in your region"),
    ),
    Concept.ENTER_GAME: _ps(
        sc=("进入游戏", "开始游戏", "踏入仙途", "开始冒险"),
        tc=("進入遊戲", "開始遊戲", "踏入仙途", "開始冒險"),
        en=("enter game", "start game", "play now"),
    ),
    Concept.START_GAME: _ps(
        sc=("开始游戏", "踏入仙途"),
        tc=("開始遊戲", "踏入仙途"),
        en=("start game", "start"),
    ),
    Concept.BARE_ENTER: _ps(
        sc=("进入",),
        tc=("進入",),
        en=("enter",),
    ),
    Concept.ENTER_WORLD: _ps(
        sc=("进入世界", "进入游戏", "开始游戏", "创建角色", "创角"),
        tc=("進入世界", "進入遊戲", "開始遊戲", "創建角色", "創角"),
        en=("enter world", "enter game", "start game", "click to create", "create role"),
    ),
    Concept.CONFIRM: _ps(
        sc=("确定", "确认", "我知道了", "我已知晓"),
        tc=("確定", "確認", "我知道了", "我已知曉"),
        en=("ok", "agree", "confirm", "close"),
    ),
    Concept.DISMISS_CLOSE: _ps(
        sc=("关闭", "关 闭", "我知道了", "今日不再", "不再提示", "点击空白", "点击空白处关闭"),
        tc=("關閉", "關 閉", "今日不再", "不再提示", "點擊空白", "點擊空白處關閉"),
        en=("close", "confirm", "×", "x", "tap close", "click blank", "click empty", "tap empty", "press anywhere"),
    ),
    Concept.SKIP: _ps(
        sc=("跳过",),
        tc=("跳過",),
        en=("skip",),
    ),
    Concept.CONTINUE: _ps(
        sc=("继续", "下一步", "点击继续", "点击屏幕", "重试"),
        tc=("繼續", "下一步", "點擊繼續", "點擊螢幕", "重試"),
        en=("continue", "next step", "retry"),
    ),
    Concept.AGREE: _ps(
        sc=("同意", "接受"),
        tc=("同意", "接受"),
        en=("agree", "accept"),
    ),
    Concept.CANCEL: _ps(
        sc=("取消", "不同意"),
        tc=("取消", "不同意"),
        en=("cancel", "decline"),
    ),
    Concept.PRIVACY: _ps(
        sc=("个人信息保护", "隐私政策", "用户协议", "许可及服务", "已阅读并同意"),
        tc=("個人信息保護", "隱私政策", "用戶協議", "許可及服務", "已閱讀並同意"),
        en=("protect privacy", "privacy policy", "terms of service"),
    ),
    Concept.PRIVACY_TERMS: _ps(
        sc=("个人信息保护", "隐私政策", "用户协议", "许可及服务", "已阅读并同意"),
        tc=("個人信息保護", "隱私政策", "用戶協議", "許可及服務", "已閱讀並同意"),
        en=("protect privacy", "privacy policy"),
    ),
    Concept.PRIVACY_DISAGREE: _ps(
        sc=("不同意", "拒绝"),
        tc=("不同意", "拒絕"),
        en=("decline", "reject"),
    ),
    Concept.PRIVACY_MODAL_CONSENT: _ps(
        sc=("同意并进入", "同意", "接受", "确认"),
        tc=("同意並進入", "同意", "接受", "確認"),
        en=("agree and enter", "agree", "accept", "confirm"),
    ),
    Concept.ANNOUNCEMENT: _ps(
        sc=("公告", "日常通知", "活动", "点击空白", "今日不再", "不再提示"),
        tc=("公告", "日常通知", "活動", "點擊空白", "今日不再", "不再提示"),
        en=("announcement", "notice", "event"),
    ),
    Concept.OVERLAY: _ps(
        sc=("日常通知", "公告", "活动", "点击空白", "不再提示", "通知", "遮挡", "弹窗"),
        tc=("日常通知", "公告", "活動", "點擊空白", "不再提示", "通知", "遮擋", "彈窗"),
        en=("notice", "announcement", "modal", "overlay", "popup", "covering"),
    ),
    Concept.DAILY_NOTICE: _ps(
        sc=("日常通知",),
        tc=("日常通知",),
        en=("notice", "daily notice"),
    ),
    Concept.HEALTH_ADVISORY: _ps(
        sc=("适龄", "通龄", "健康", "16岁", "本游戏适合", "cadpa"),
        tc=("適齡", "通齡", "健康", "16歲", "本遊戲適合", "cadpa"),
        en=("health advisory", "cadpa"),
        variants=("16+",),
    ),
    Concept.EXCLUDE_AUTH_CONTEXT: _ps(
        sc=("账号", "密码", "登录", "协议", "隐私", "版本"),
        tc=("帳號", "密碼", "登入", "協議", "隱私", "版本"),
        en=(
            "sub-account", "subaccount", "login", "password", "privacy", "agree",
            "copyright", "publisher", "support", "forgot", "health advisory",
        ),
    ),
    Concept.INSTALL: _ps(
        sc=("安装",),
        tc=("安裝",),
        en=("install",),
    ),
    Concept.INSTALL_DETAILS: _ps(
        sc=("更多详情",),
        tc=("更多詳情",),
        en=("more details", "detail"),
    ),
    Concept.INSTALL_ANYWAY: _ps(
        sc=("仍要安装",),
        tc=("仍要安裝",),
        en=("install anyway", "install still"),
    ),
    Concept.CONTINUE_INSTALL: _ps(
        sc=("继续安装",),
        tc=("繼續安裝",),
        en=("continue install",),
    ),
    Concept.TUTORIAL: _ps(
        sc=("点击", "引导", "教程", "手指", "轻触"),
        tc=("點擊", "引導", "教程", "手指", "輕觸"),
        en=("tutorial", "tap"),
    ),
    Concept.LOADING: _ps(
        sc=("加载", "请稍候", "正在进入", "连接中", "载入"),
        tc=("載入", "請稍候", "正在進入", "連接中", "載入"),
        en=("loading", "please wait"),
    ),
    Concept.CHAR_SLOT: _ps(
        sc=("等级", "选择角色", "已有角色", "创角", "创建角色"),
        tc=("等級", "選擇角色", "已有角色", "創角", "創建角色"),
        en=("lv.", "lv ", "select character"),
    ),
    Concept.PK_AGREEMENT: _ps(
        sc=("pk玩法", "接受", "pk"),
        tc=("pk玩法", "接受", "pk"),
        en=("pk",),
    ),
    Concept.TECHNIQUE: _ps(
        en=("technique",),
    ),
    Concept.SELECTION: _ps(
        en=("selection",),
    ),
    Concept.SPATIAL_BUTTON: _ps(
        sc=("开始游戏", "进入游戏", "踏入", "确定", "确认", "继续", "下一步"),
        tc=("開始遊戲", "進入遊戲", "踏入", "確定", "確認", "繼續", "下一步"),
        en=("start game", "enter game", "confirm", "continue", "next"),
    ),
    Concept.TUTORIAL_TAP_CARD: _ps(
        sc=("点击卡牌", "点击卡片", "点选卡牌"),
        tc=("點擊卡牌", "點擊卡片", "點選卡牌"),
        en=("click the card", "tap the card", "select the card"),
    ),
    Concept.TUTORIAL_DEPLOY: _ps(
        sc=("上阵", "拖拽", "拖曳", "部署"),
        tc=("上陣", "拖拽", "拖曳", "部署"),
        en=("deploy", "drag", "place on field"),
    ),
    Concept.TUTORIAL_TAP_GLOW: _ps(
        sc=("点我放必杀", "放必杀", "点击必杀", "点我", "点击我"),
        tc=("點我放必殺", "放必殺", "點擊必殺", "點我", "點擊我"),
        en=("tap me", "unleash ultimate", "use ultimate", "tap to unleash"),
    ),
}

# Backward-compatible flat tuples (re-exported from utils)
CHARACTER_CREATION_OCR_MARKERS: tuple[str, ...] = PHRASES[Concept.CHARACTER_CREATION].all_locales()
IN_GAME_HUD_OCR_MARKERS: tuple[str, ...] = PHRASES[Concept.IN_GAME_HUD].all_locales()
