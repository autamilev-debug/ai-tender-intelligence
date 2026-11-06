"""Dynamic registered identities and public Telegram ingress; no username trust."""
from collections.abc import Set
from sqlalchemy import select,func
from .schema import users


class RegisteredUsers(Set):
    def __init__(self,db):self.db=db
    def __contains__(self,user):
        if type(user) is not int or not 0<user<2**63:return False
        with self.db.transaction() as s:
            return s.scalar(select(users.c.id).where(users.c.scope==self.db.settings.scope,
                users.c.telegram_user_id==user,users.c.status=='ACTIVE')) is not None
    def __iter__(self):
        with self.db.transaction() as s:
            values=list(s.scalars(select(users.c.telegram_user_id).where(
                users.c.scope==self.db.settings.scope,users.c.status=='ACTIVE')))
        return iter(values)
    def __len__(self):
        with self.db.transaction() as s:
            return s.scalar(select(func.count()).select_from(users).where(
                users.c.scope==self.db.settings.scope,users.c.status=='ACTIVE'))


class PublicTelegramUsers(Set):
    """Only ingress is open. Database registration/ownership gates stay separate."""
    def __contains__(self,user):return type(user) is int and 0<user<2**63
    def __iter__(self):return iter(())
    def __len__(self):return 0


def freeze_users(allowed):
    return allowed if isinstance(allowed,RegisteredUsers) else frozenset(allowed)
