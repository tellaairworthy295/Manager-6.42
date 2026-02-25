import pandas as pd
import phoenixc
from datetime import datetime, timedelta
import pymysql

def save_data_to_mysql(data, trading_day=None):
    """
    将主力合约数据保存到MySQL数据库
    
    参数:
        dominant_data: phoenixc返回的主力合约Series数据
        trading_day: 交易日期,格式"YYYYMMDD",默认None(使用当前日期)
    """
    if data is None or len(data) == 0:
        return
    
    # 处理交易日期
    if trading_day is None:
        trading_day = datetime.now().strftime("%Y%m%d")
    
    # 连接数据库
    con = pymysql.connect(
        host='10.29.88.63',
        port=3306,
        user='lmh',
        passwd='lmh@123456',
        db='selfdev_test',
        charset='utf8mb4'
    )
    
    try:
        cursor = con.cursor()
        
        # 构建SQL语句(使用REPLACE INTO实现插入或更新)
        sql = """
        REPLACE INTO etf_tf_dominant 
        (TradingDay, TFCode, UpdateTime) 
        VALUES (%s, %s, %s)
        """
        
        update_time = datetime.now().strftime("%Y%m%d-%H:%M:%S")
        
        # 获取主力合约代码
        tf_code = dominant_data.iloc[0]
        
        values = (
            trading_day,
            tf_code,
            update_time
        )
        
        cursor.execute(sql, values)
        con.commit()
        
        print(f"主力合约数据已保存: TradingDay={trading_day}, TFCode={tf_code}")
        
    except Exception as e:
        print(f"保存到数据库失败: {e}")
        con.rollback()
    finally:
        cursor.close()
        con.close()


# 使用示例
if __name__ == "__main__":
    phoenixc.init("guhao_ind","kQ4@ks0u",("10.29.92.42",9081))
    
    # 获取当日日期
    today = datetime.now()
    from datetime import timedelta
    date = (today - timedelta(days=0)).strftime("%Y-%m-%d")
    #today = '2025-12-03'
    # 获取主力合约
    tick_df = phoenixc.get_price(unified_code='000002.SZ', start_date=date, end_date=date, frequency='1m', time_slice=("09:25:00", "15:00"))
    # tick_df["per_tick_volume"] = tick_df["volume"].diff().fillna(tick_df["volume"])
    # # Sometimes the first volume can be negative or wrong after diff, make sure all >=0
    # tick_df["per_tick_volume"] = tick_df["per_tick_volume"].clip(lower=0)
    
    print(f"主力合约数据: \n{tick_df}")
    
    # 保存到数据库
    # if rs is not None and len(rs) > 0:
    #     save_dominant_to_mysql(rs, trading_day=today.replace('-', ''))