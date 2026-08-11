# InvenTree real chain 01

- `attempt-01`: context-marker schema stop из-за формата warnings; downstream не запускался.
- `attempt-02`: пять skill stages прошли, execution выявил 6/6 HTTP 403 из-за пропущенной project-native view role.
- `attempt-03`: permission seam исправлен; generated 6/6 и PartCategory regression 17/17 прошли. Full backend 1471 выполнен, но 27 unrelated Windows/CI-setup failures не позволяют заявить полный project green.

Актуальный generated test: `D:\AI-Projects\real-chain-projects\inventree-clean\src\backend\InvenTree\part\test_real_chain.py`.
