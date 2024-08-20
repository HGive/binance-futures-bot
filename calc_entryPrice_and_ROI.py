def calculate_average_price(avbl, buy_percentages, price_diffs):
    initial_price = 1000  # 첫 매수 가격
    total_cost = 0  # 총 비용
    total_quantity = 0  # 총 수량

    # 초기 자본금
    remaining_avbl = avbl
    average_price = initial_price  # 첫 매수 시점의 평단가

    for i, (buy_percentage, price_diff) in enumerate(zip(buy_percentages, price_diffs)):
        # 현재 매수 가격 계산 (이전 평단가에서 price_diff만큼 감소)
        buy_price = average_price * price_diff
        
        # 매수할 금액 계산
        buy_amount = remaining_avbl * buy_percentage
        
        # 매수할 수량 계산
        buy_quantity = buy_amount / buy_price
        
        # 총 비용과 총 수량 업데이트
        total_cost += buy_amount
        total_quantity += buy_quantity
        
        # 새로운 평단가 계산
        average_price = total_cost / total_quantity
        
        # 현재 가격이 평단가에서 몇 퍼센트 차이인지 계산
        percentage_diff = (buy_price - average_price) / average_price * 100
        
        # 결과 출력
        print(f"{i+1}차 매수 후 평단가: {average_price:.2f} USD, 현재 가격: {buy_price:.2f} USD, 평단가 대비 {percentage_diff:.2f}% 차이")
        
        # 남은 자본금 업데이트
        remaining_avbl -= buy_amount

    return average_price

# 초기 설정
avbl = 1000  # 초기 자본금
# buy_percent = [0.02, 0.06, 0.25, 1]  # 각 매수 퍼센트 (1차, 2차, 3차, 4차 매수)
# price_diffs = [  1, 0.97, 0.94, 0.91]  # 각 매수 시점에서의 가격 변동 비율

buy_percent = [0.02, 0.06, 0.25, 1]  # 각 매수 퍼센트 (1차, 2차, 3차, 4차 매수)
price_diffs = [1, 0.97, 0.96, 0.94]  # 각 매수 시점에서의 가격 변동 비율

# buy_percent = [0.02, 0.06, 0.25, 1]  # 각 매수 퍼센트 (1차, 2차, 3차, 4차 매수)
# price_diffs = [1, 0.98, 0.96, 0.94]  # 각 매수 시점에서의 가격 변동 비율

# buy_percent = [0.3, 1, 0.25, 1]  # 각 매수 퍼센트 (1차, 2차, 3차, 4차 매수)
# price_diffs = [  1, 0.97, 0.96, 0.94]  # 각 매수 시점에서의 가격 변동 비율


# 함수 호출
final_average_price = calculate_average_price(avbl, buy_percent, price_diffs)