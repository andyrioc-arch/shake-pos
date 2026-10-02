"""Las dos guardas de solo lectura que dependen del código.

La tercera es el rol de Postgres: aunque alguien quite estas, un usuario con
solo SELECT no puede escribir. Estas existen para que un error de código se
vea en las pruebas contra SQLite, y para negarse a arrancar con la cadena
equivocada en vez de descubrirlo el día que algo escriba.
"""
from contextlib import contextmanager

from django.db import connection, transaction


class RolConEscritura(RuntimeError):
    """La conexión de producción puede escribir. El servidor no arranca."""


@contextmanager
def solo_lectura():
    """Una transacción que la base rechaza si algo intenta escribir.

    En SQLite, `PRAGMA query_only` vale para la conexión entera, así que se
    apaga al salir: las pruebas siembran datos en la misma conexión. En
    Postgres, `SET TRANSACTION READ ONLY` tiene que ser lo primero de la
    transacción, y solo se puede pedir si esta es la de más afuera.
    """
    sqlite = connection.vendor == "sqlite"
    pide_read_only = (connection.vendor == "postgresql"
                      and not connection.in_atomic_block)
    if sqlite:
        with connection.cursor() as c:
            c.execute("PRAGMA query_only = ON")
    try:
        with transaction.atomic():
            if pide_read_only:
                with connection.cursor() as c:
                    c.execute("SET TRANSACTION READ ONLY")
            yield
    finally:
        if sqlite:
            with connection.cursor() as c:
                c.execute("PRAGMA query_only = OFF")


def verificar_rol():
    """Contra Postgres: el usuario no puede escribir y nace en solo lectura.

    `has_table_privilege` sobre la tabla de ventas basta como muestra: el rol
    se crea con SELECT sobre todo el esquema, y un superusuario o el dueño de
    las tablas dan verdadero aquí.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as c:
        c.execute("""
            select current_user,
                   has_table_privilege('inventario_venta', 'INSERT')
                   or has_table_privilege('inventario_venta', 'UPDATE')
                   or has_table_privilege('inventario_venta', 'DELETE'),
                   current_setting('default_transaction_read_only')
        """)
        usuario, escribe, read_only = c.fetchone()
    if escribe:
        raise RolConEscritura(
            f"El usuario {usuario!r} puede escribir en inventario_venta. "
            "SHAKE_MCP_DATABASE_URL debe usar el rol de solo lectura.")
    if read_only != "on":
        raise RolConEscritura(
            f"El usuario {usuario!r} no tiene default_transaction_read_only. "
            "Corre el ALTER ROLE del SQL de instalación.")
