;;; Common Lisp examples for "Dear Lisp, sorry about Rust".
;;; Run with: sbcl --script examples.lisp
;;; These are not extensions to the Rust experiment's lisp! macro.

(in-package #:cl-user)

(assert (= (+ 2 (* 3 4)) 14))
(assert (equal '(+ 2 (* 3 4)) (list '+ 2 (list '* 3 4))))

(defmacro when-curious (condition &body body)
  `(if ,condition
       (progn ,@body)
       nil))

(assert
 (equal
  (macroexpand-1
   '(when-curious (> 3 2)
      (write-line "Still not writing a device driver.")
      (+ 2 3)))
  '(if (> 3 2)
       (progn
         (write-line "Still not writing a device driver.")
         (+ 2 3))
       nil)))

;; A false condition is evaluated once and leaves the body alone.
(let ((calls 0))
  (assert (null (when-curious (progn (incf calls) nil)
                  (error "The unchosen body must stay unevaluated."))))
  (assert (= calls 1)))

;; A true condition is also evaluated once; its body runs in order.
(let ((calls 0)
      (events nil))
  (assert (= (when-curious (progn (incf calls) t)
               (push :first events)
               (push :second events)
               (+ 2 3))
             5))
  (assert (= calls 1))
  (assert (equal (reverse events) '(:first :second))))

(assert (= (when-curious (> 3 2)
             (write-line "Still not writing a device driver.")
             (+ 2 3))
           5))

(write-line "Reader, quotation, macro expansion, and evaluation checks passed.")
