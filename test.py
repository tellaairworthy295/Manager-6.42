

from utils.database import UsersRepository, get_db_manager


db_manager = get_db_manager()
user_repo = UsersRepository(db_manager)

user_id = user_repo.get_user_id_by_email("1026334385@qq.com")
print(user_id)