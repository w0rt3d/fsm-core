"""
setup.py — упаковка fsm_core как устанавливаемого дистрибутива.

В исходном репозитории такого файла не было (пакет использовался только
через запуск из корня репозитория с `pip install -r requirements.txt`).
Этот файл добавлен при сборке документации/проекта и не описывает
какую-либо функциональность самой библиотеки — только метаданные
дистрибуции. Версия ниже — техническая (в коде пакета нет атрибута
`__version__`, отталкиваться не от чего) и не подразумевает историю
релизов, зафиксированную где-либо ещё.
"""
from pathlib import Path

from setuptools import find_packages, setup

ROOT = Path(__file__).parent
long_description = (ROOT / "README.md").read_text(encoding="utf-8")

setup(
    name="fsm_core",
    version="1.1.0",
    description=(
        "In-memory DAG engine: typed nodes (pydantic), iterative graph "
        "algorithms, a priority event bus, and a plugin system with a "
        "built-in circuit breaker."
    ),
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(exclude=["tests", "tests.*"]),
    python_requires=">=3.9",
    install_requires=[
        "pydantic>=2.0",
    ],
    extras_require={
        "dev": ["pytest>=7.0"],
        "bson": ["pymongo>=4.0"],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Operating System :: OS Independent",
    ],
)
