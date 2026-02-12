import phoenixc
username = 'jsw_uat'
password = 'uN5,uaz0'
ip = '10.50.184.237'
port = 9581
phoenixc.init(username, password, addr=(ip, port))
rs = phoenixc.futures.get_dominant(underlying_symbol='IF',
                                   start_date='2024-03-01', end_date='2024-03-30')
print(rs)