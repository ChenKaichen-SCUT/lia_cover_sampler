(declare-const x Int)
(declare-const y Int)
(declare-const z Int)
(assert (and (= (+ x y) 10) (or (= x 0) (= x 10)) (>= z 0) (<= z 10)))
