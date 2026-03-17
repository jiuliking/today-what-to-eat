import os
import random
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from sqlalchemy import ForeignKey, String, create_engine, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker, selectinload

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR.parent / 'data.db'}")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-secret")

engine = create_engine(DB_PATH, connect_args={"check_same_thread": False} if DB_PATH.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine)
serializer = URLSafeSerializer(SECRET_KEY, salt="lunch-decider")


class Base(DeclarativeBase):
    pass


class Canteen(Base):
    __tablename__ = "canteens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    weight: Mapped[int] = mapped_column(default=10)
    distance_level: Mapped[str] = mapped_column(String(20), default="medium")
    dishes: Mapped[list["Dish"]] = relationship(back_populates="canteen", cascade="all, delete-orphan")


class Dish(Base):
    __tablename__ = "dishes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(String(255), default="")
    weight: Mapped[int] = mapped_column(default=10)
    canteen_id: Mapped[int] = mapped_column(ForeignKey("canteens.id"))
    canteen: Mapped[Canteen] = relationship(back_populates="dishes")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), default="")


app = FastAPI(title="今天吃什么")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


DEFAULT_SETTINGS = {
    "show_weights_frontend": "true",
    "hero_line1_enabled": "true",
    "hero_line1_text": "帮你终结吃饭纠结症",
    "hero_line2_enabled": "true",
    "hero_line2_text": "今天到底吃什么？",
    "hero_line3_enabled": "true",
    "hero_line3_text": "选个模式，点一下就出结果。",
}
DISTANCE_OPTIONS = ["near", "medium", "far"]
DISTANCE_LABELS = {"near": "近", "medium": "中", "far": "远"}
SCENE_OPTIONS = ["default", "rainy", "lazy"]
SCENE_LABELS = {"default": "默认", "rainy": "下雨天", "lazy": "不想走远"}
SCENE_DISTANCE_MULTIPLIERS = {
    "default": {"near": 1.0, "medium": 1.0, "far": 1.0},
    "rainy": {"near": 1.8, "medium": 1.2, "far": 0.6},
    "lazy": {"near": 2.2, "medium": 1.1, "far": 0.45},
}


def clamp_weight(weight: int) -> int:
    return max(1, min(20, int(weight)))


def normalize_distance_level(distance_level: str) -> str:
    return distance_level if distance_level in DISTANCE_OPTIONS else "medium"


def get_scene_multiplier(scene: str, distance_level: str) -> float:
    scene_key = scene if scene in SCENE_OPTIONS else "default"
    distance_key = normalize_distance_level(distance_level)
    return SCENE_DISTANCE_MULTIPLIERS[scene_key][distance_key]


def weighted_pick(items, weight_getter):
    if not items:
        return None
    weights = [max(0.01, float(weight_getter(item))) for item in items]
    return random.choices(items, weights=weights, k=1)[0]


def ensure_schema_updates() -> None:
    if not DB_PATH.startswith("sqlite"):
        return
    with engine.begin() as conn:
        canteen_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(canteens)"))}
        if "weight" not in canteen_columns:
            conn.execute(text("ALTER TABLE canteens ADD COLUMN weight INTEGER DEFAULT 10"))
        if "distance_level" not in canteen_columns:
            conn.execute(text("ALTER TABLE canteens ADD COLUMN distance_level VARCHAR(20) DEFAULT 'medium'"))
        dish_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(dishes)"))}
        if "weight" not in dish_columns:
            conn.execute(text("ALTER TABLE dishes ADD COLUMN weight INTEGER DEFAULT 10"))


def get_setting(db: Session, key: str, default: str = "") -> str:
    setting = db.get(AppSetting, key)
    return setting.value if setting else default


def set_setting(db: Session, key: str, value: str) -> None:
    setting = db.get(AppSetting, key)
    if setting:
        setting.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def load_ui_settings(db: Session) -> dict:
    return {key: get_setting(db, key, default) for key, default in DEFAULT_SETTINGS.items()}


def serialize_scene(scene: str) -> dict:
    scene_key = scene if scene in SCENE_OPTIONS else "default"
    return {"key": scene_key, "label": SCENE_LABELS[scene_key]}


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_schema_updates()
    with SessionLocal() as db:
        if db.scalar(select(func.count(Canteen.id))) == 0:
            seed_data(db)
        changed = False
        for key, default in DEFAULT_SETTINGS.items():
            if not db.get(AppSetting, key):
                db.add(AppSetting(key=key, value=default))
                changed = True
        if changed:
            db.commit()


def seed_data(db: Session) -> None:
    examples = [
        {
            "name": "一食堂",
            "description": "离教学楼近，出餐快",
            "weight": 12,
            "distance_level": "near",
            "dishes": [
                ("红烧肉套餐", "稳妥派", 15),
                ("番茄鸡蛋面", "热乎乎", 10),
                ("香辣鸡腿饭", "下饭", 14),
            ],
        },
        {
            "name": "二食堂",
            "description": "选择多，适合纠结症",
            "weight": 10,
            "distance_level": "medium",
            "dishes": [
                ("麻辣香锅", "想吃重口就它", 16),
                ("牛肉粉丝汤", "清爽一点", 9),
                ("黄焖鸡米饭", "经典不踩雷", 13),
            ],
        },
        {
            "name": "清真窗口",
            "description": "面食不错",
            "weight": 8,
            "distance_level": "far",
            "dishes": [
                ("牛肉拉面", "汤面选手", 14),
                ("孜然牛肉盖饭", "香", 12),
            ],
        },
    ]
    for item in examples:
        canteen = Canteen(
            name=item["name"],
            description=item["description"],
            weight=item["weight"],
            distance_level=item["distance_level"],
        )
        db.add(canteen)
        db.flush()
        for dish_name, note, dish_weight in item["dishes"]:
            db.add(Dish(name=dish_name, note=note, weight=dish_weight, canteen_id=canteen.id))
    db.commit()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


def is_logged_in(request: Request) -> bool:
    token = request.cookies.get("admin_token")
    if not token:
        return False
    try:
        data = serializer.loads(token)
        return data.get("role") == "admin"
    except Exception:
        return False


def require_login(request: Request):
    if not is_logged_in(request):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    return None


def render_home(request: Request, canteens, ui_settings, result=None, selected_scene="default", selected_mode="canteen", selected_canteen_id=""):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "canteens": canteens,
        "result": result,
        "show_weights_frontend": ui_settings["show_weights_frontend"] == "true",
        "ui_settings": ui_settings,
        "selected_scene": selected_scene,
        "selected_mode": selected_mode,
        "selected_canteen_id": str(selected_canteen_id or ""),
        "scene_labels": SCENE_LABELS,
    })


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with SessionLocal() as db:
        canteens = db.scalars(
            select(Canteen).options(selectinload(Canteen.dishes)).order_by(Canteen.name)
        ).all()
        ui_settings = load_ui_settings(db)
    return render_home(request, canteens, ui_settings, result=None, selected_scene="default", selected_mode="canteen", selected_canteen_id="")


@app.post("/random", response_class=HTMLResponse)
def random_pick(
    request: Request,
    mode: str = Form(...),
    canteen_id: Optional[str] = Form(default=None),
    scene: str = Form(default="default"),
):
    result = {"title": "今天吃什么", "content": "暂无结果", "sub": ""}
    selected_scene = scene if scene in SCENE_OPTIONS else "default"

    with SessionLocal() as db:
        canteens = db.scalars(
            select(Canteen).options(selectinload(Canteen.dishes)).order_by(Canteen.name)
        ).all()
        ui_settings = load_ui_settings(db)
        show_weights_frontend = ui_settings["show_weights_frontend"] == "true"

        parsed_canteen_id = int(canteen_id) if canteen_id and canteen_id.strip() else None

        if mode == "canteen":
            picked = weighted_pick(
                canteens,
                lambda x: x.weight * get_scene_multiplier(selected_scene, x.distance_level),
            )
            if picked:
                sub_parts = [picked.description] if picked.description else []
                sub_parts.append(f"距离：{DISTANCE_LABELS[normalize_distance_level(picked.distance_level)]}")
                sub_parts.append(f"场景：{SCENE_LABELS[selected_scene]}")
                if show_weights_frontend:
                    final_weight = x_weight = picked.weight * get_scene_multiplier(selected_scene, picked.distance_level)
                    sub_parts.append(f"基础 {picked.weight} × 场景 {get_scene_multiplier(selected_scene, picked.distance_level):.2f} = {final_weight:.2f}")
                result = {"title": "食堂", "content": picked.name, "sub": " · ".join([s for s in sub_parts if s])}
        elif mode == "canteen_dish":
            picked_canteen = None
            if parsed_canteen_id:
                picked_canteen = db.get(Canteen, parsed_canteen_id)
                if picked_canteen:
                    _ = picked_canteen.dishes
            else:
                canteens_with_dishes = [c for c in canteens if c.dishes]
                picked_canteen = weighted_pick(
                    canteens_with_dishes,
                    lambda x: x.weight * get_scene_multiplier(selected_scene, x.distance_level),
                )

            if picked_canteen and picked_canteen.dishes:
                picked_dish = weighted_pick(picked_canteen.dishes, lambda x: x.weight)
                if picked_dish:
                    sub_parts = [f"场景：{SCENE_LABELS[selected_scene]}", f"距离：{DISTANCE_LABELS[normalize_distance_level(picked_canteen.distance_level)]}"]
                    if picked_dish.note:
                        sub_parts.append(picked_dish.note)
                    if show_weights_frontend:
                        canteen_final = picked_canteen.weight * get_scene_multiplier(selected_scene, picked_canteen.distance_level)
                        sub_parts.append(
                            f"食堂 {picked_canteen.weight} × {get_scene_multiplier(selected_scene, picked_canteen.distance_level):.2f} = {canteen_final:.2f}"
                        )
                        sub_parts.append(f"菜品权重 {picked_dish.weight}")
                    result = {
                        "title": "食堂+菜",
                        "content": f"{picked_canteen.name} · {picked_dish.name}",
                        "sub": " · ".join(sub_parts),
                    }
        elif mode == "dish":
            dishes = db.scalars(select(Dish).options(selectinload(Dish.canteen)).order_by(Dish.name)).all()
            picked = weighted_pick(
                dishes,
                lambda x: x.weight * get_scene_multiplier(selected_scene, x.canteen.distance_level),
            )
            if picked:
                sub_parts = [
                    f"来自 {picked.canteen.name}",
                    f"场景：{SCENE_LABELS[selected_scene]}",
                    f"距离：{DISTANCE_LABELS[normalize_distance_level(picked.canteen.distance_level)]}",
                ]
                if picked.note:
                    sub_parts.append(picked.note)
                if show_weights_frontend:
                    final_weight = picked.weight * get_scene_multiplier(selected_scene, picked.canteen.distance_level)
                    sub_parts.append(
                        f"菜品 {picked.weight} × 场景 {get_scene_multiplier(selected_scene, picked.canteen.distance_level):.2f} = {final_weight:.2f}"
                    )
                result = {"title": "菜品", "content": picked.name, "sub": " · ".join(sub_parts)}

    return render_home(
        request,
        canteens,
        ui_settings,
        result=result,
        selected_scene=selected_scene,
        selected_mode=mode,
        selected_canteen_id=parsed_canteen_id or "",
    )


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/admin/login")
def admin_login(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse("login.html", {"request": request, "error": "密码不对，再试试"}, status_code=400)
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("admin_token", serializer.dumps({"role": "admin"}), httponly=True, samesite="lax")
    return response


@app.post("/admin/logout")
def admin_logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("admin_token")
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    login_redirect = require_login(request)
    if login_redirect:
        return login_redirect
    with SessionLocal() as db:
        canteens = db.scalars(select(Canteen).order_by(Canteen.id.desc())).all()
        dishes = db.scalars(select(Dish).options(selectinload(Dish.canteen)).order_by(Dish.id.desc())).all()
        ui_settings = load_ui_settings(db)
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "canteens": canteens,
        "dishes": dishes,
        "show_weights_frontend": ui_settings["show_weights_frontend"] == "true",
        "ui_settings": ui_settings,
        "distance_labels": DISTANCE_LABELS,
    })


@app.post("/admin/settings")
def update_settings(
    request: Request,
    show_weights_frontend: Optional[str] = Form(default=None),
    hero_line1_enabled: Optional[str] = Form(default=None),
    hero_line1_text: str = Form(default=""),
    hero_line2_enabled: Optional[str] = Form(default=None),
    hero_line2_text: str = Form(default=""),
    hero_line3_enabled: Optional[str] = Form(default=None),
    hero_line3_text: str = Form(default=""),
):
    login_redirect = require_login(request)
    if login_redirect:
        return login_redirect
    with SessionLocal() as db:
        set_setting(db, "show_weights_frontend", "true" if show_weights_frontend == "on" else "false")
        set_setting(db, "hero_line1_enabled", "true" if hero_line1_enabled == "on" else "false")
        set_setting(db, "hero_line1_text", hero_line1_text.strip())
        set_setting(db, "hero_line2_enabled", "true" if hero_line2_enabled == "on" else "false")
        set_setting(db, "hero_line2_text", hero_line2_text.strip())
        set_setting(db, "hero_line3_enabled", "true" if hero_line3_enabled == "on" else "false")
        set_setting(db, "hero_line3_text", hero_line3_text.strip())
        db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/canteens")
def create_canteen(
    request: Request,
    name: str = Form(...),
    description: str = Form(default=""),
    weight: int = Form(default=10),
    distance_level: str = Form(default="medium"),
):
    login_redirect = require_login(request)
    if login_redirect:
        return login_redirect
    with SessionLocal() as db:
        db.add(
            Canteen(
                name=name.strip(),
                description=description.strip(),
                weight=clamp_weight(weight),
                distance_level=normalize_distance_level(distance_level),
            )
        )
        db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/canteens/{canteen_id}/update")
def update_canteen(
    request: Request,
    canteen_id: int,
    name: str = Form(...),
    description: str = Form(default=""),
    weight: int = Form(default=10),
    distance_level: str = Form(default="medium"),
):
    login_redirect = require_login(request)
    if login_redirect:
        return login_redirect
    with SessionLocal() as db:
        canteen = db.get(Canteen, canteen_id)
        if canteen:
            canteen.name = name.strip()
            canteen.description = description.strip()
            canteen.weight = clamp_weight(weight)
            canteen.distance_level = normalize_distance_level(distance_level)
            db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/canteens/{canteen_id}/delete")
def delete_canteen(request: Request, canteen_id: int):
    login_redirect = require_login(request)
    if login_redirect:
        return login_redirect
    with SessionLocal() as db:
        canteen = db.get(Canteen, canteen_id)
        if canteen:
            db.delete(canteen)
            db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/dishes")
def create_dish(
    request: Request,
    name: str = Form(...),
    note: str = Form(default=""),
    canteen_id: int = Form(...),
    weight: int = Form(default=10),
):
    login_redirect = require_login(request)
    if login_redirect:
        return login_redirect
    with SessionLocal() as db:
        db.add(Dish(name=name.strip(), note=note.strip(), canteen_id=canteen_id, weight=clamp_weight(weight)))
        db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/dishes/{dish_id}/update")
def update_dish(
    request: Request,
    dish_id: int,
    name: str = Form(...),
    note: str = Form(default=""),
    canteen_id: int = Form(...),
    weight: int = Form(default=10),
):
    login_redirect = require_login(request)
    if login_redirect:
        return login_redirect
    with SessionLocal() as db:
        dish = db.get(Dish, dish_id)
        if dish:
            dish.name = name.strip()
            dish.note = note.strip()
            dish.canteen_id = canteen_id
            dish.weight = clamp_weight(weight)
            db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/dishes/{dish_id}/delete")
def delete_dish(request: Request, dish_id: int):
    login_redirect = require_login(request)
    if login_redirect:
        return login_redirect
    with SessionLocal() as db:
        dish = db.get(Dish, dish_id)
        if dish:
            db.delete(dish)
            db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
