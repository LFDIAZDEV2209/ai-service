"""Runner de desarrollo para Windows — delega en `main.run_server`.

Mantiene el host local (127.0.0.1) y sin reload; la selección del
`SelectorEventLoop` (requisito psycopg async) vive en `main.py` para que
`python main.py` y `python run_dev.py` compartan la misma lógica.
"""

from main import run_server

if __name__ == "__main__":
    run_server(host="127.0.0.1", reload=False)
