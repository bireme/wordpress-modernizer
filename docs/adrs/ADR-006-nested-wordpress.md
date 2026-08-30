# ADR-006: WordPress aninhado

Status: aceito. Instalações independentes aninhadas formam uma árvore sem relação com multisite.
Os planos são determinísticos, e as cópias pai excluem raízes filhas e artefatos de execução para
evitar sobrescritas.
