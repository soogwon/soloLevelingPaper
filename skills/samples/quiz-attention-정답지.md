# Attention Is All You Need 이해도 퀴즈 — 정답지

- 논문: Vaswani et al., Attention Is All You Need (arXiv 1706.03762v7)
- 학습 목적: 이해
- 문항: 10개 (정의 4, 관계 3, 적용 3)
- 안내: 샘플: solo-leveling-quiz 기본값(10문항, 정의 4 · 관계 3 · 적용 3)으로 만든 예시입니다. 자료 없이 10분 안에 풀어 보세요.

**1. 정답:** ① 순환과 합성곱 없이 어텐션만으로 입력과 출력의 관계를 잡는 구조
   - 해설: 논문은 순환과 합성곱을 완전히 빼고 어텐션 메커니즘에만 기대는 구조로 트랜스포머를 소개한다.
   - 근거: p.1 "based solely on attention mechanisms, dispensing with recurrence and convolutions entirely"

**2. 정답:** 한 시퀀스 안의 서로 다른 위치들 사이의 관계
   - 채점 기준: '같은 시퀀스(문장) 안의 위치끼리'라는 뜻이 있으면 정답. '입력과 출력 사이'만 쓰면 오답.
   - 해설: 셀프 어텐션은 하나의 시퀀스 안에서 서로 다른 위치를 연결해 그 시퀀스의 표현을 계산한다.
   - 근거: p.2 "an attention mechanism relating different positions of a single sequence in order to compute a representation of the sequence"

**3. 정답:** ③ 6개
   - 해설: 인코더와 디코더 모두 같은 층 N = 6개를 쌓는다. 8은 멀티헤드 어텐션의 헤드 수다.
   - 근거: p.3 "The encoder is composed of a stack of N = 6 identical layers."

**4. 정답:** ① 선형 변환 두 개 사이에 ReLU 활성화
   - 해설: 각 위치에 따로, 똑같이 적용되는 두 개의 선형 변환이며 그 사이에 ReLU가 있다.
   - 근거: p.5 "This consists of two linear transformations with a ReLU activation in between."

**5. 정답:** ④ d_k가 크면 내적 값이 커져 소프트맥스 기울기가 매우 작아지는 것을 막기 위해
   - 해설: d_k가 크면 내적의 크기가 커져 소프트맥스가 기울기가 극히 작은 영역으로 밀린다고 추정하고, 이를 상쇄하려고 나눈다.
   - 근거: p.4 "the dot products grow large in magnitude, pushing the softmax function into regions where it has extremely small gradients"

**6. 정답:** RNN은 위치를 순서대로 계산해 한 예제 안에서 병렬 처리가 안 되지만, 트랜스포머는 이 순차 계산이 없어 훨씬 많이 병렬화할 수 있다.
   - 채점 기준: 'RNN의 순차 계산(순서대로 계산)'과 '병렬화가 가능해짐' 두 요소가 모두 있으면 정답, 하나만 있으면 부분 점수.
   - 해설: RNN은 이전 은닉 상태에 의존해 순서대로 계산하므로 병렬화가 막히고, 트랜스포머는 이를 없애 병렬화를 크게 늘린다.
   - 근거: p.2 "This inherently sequential nature precludes parallelization within training examples" / p.2 "The Transformer allows for significantly more parallelization"

**7. 정답:** ③ 서로 다른 표현 부분 공간의 정보를 서로 다른 위치에서 동시에 참고할 수 있어서
   - 해설: 멀티헤드는 여러 표현 부분 공간을 동시에 보게 해 준다. 헤드 하나면 평균을 내는 과정에서 이것이 막힌다. 계산 비용은 헤드 하나와 비슷하다고 한다.
   - 근거: p.5 "Multi-head attention allows the model to jointly attend to information from different representation subspaces at different positions."

**8. 정답:** 위치 인코딩(positional encoding). 서로 다른 주파수의 사인·코사인 함수를 쓴다.
   - 채점 기준: '위치 인코딩(위치 임베딩)'이면 정답. 사인·코사인(sinusoid)까지 쓰면 만점, 위치 인코딩만 쓰면 부분 점수.
   - 해설: 순서 정보를 주려고 인코더·디코더 입력 임베딩에 위치 인코딩을 더하고, 서로 다른 주파수의 사인·코사인 함수를 쓴다.
   - 근거: p.6 "we add "positional encodings" to the input embeddings at the bottoms of the encoder and decoder stacks" / p.6 "we use sine and cosine functions of different frequencies"

**9. 정답:** ④ 소프트맥스 입력에서 허용되지 않는 연결의 값을 -∞로 가린다(마스킹)
   - 해설: 자기회귀 성질을 지키려고 스케일드 닷-프로덕트 어텐션 안에서, 소프트맥스 입력 중 허용되지 않는 연결을 -∞로 마스킹한다.
   - 근거: p.5 "We implement this inside of scaled dot-product attention by masking out (setting to −∞) all values in the input of the softmax which correspond to illegal connections."

**10. 정답:** ② 6.1절 기계 번역 결과와 표 2 (Transformer (big) 28.4 BLEU)
   - 해설: 6.1절은 big 모델이 영어→독일어에서 앙상블을 포함한 기존 최고 모델보다 2.0 BLEU 이상 높은 28.4 BLEU를 냈다고 쓰고, 표 2에 비교가 정리되어 있다.
   - 근거: p.8 "outperforms the best previously reported models (including ensembles) by more than 2.0 BLEU, establishing a new state-of-the-art BLEU score of 28.4"
