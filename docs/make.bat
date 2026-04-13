@ECHO OFF

set SOURCEDIR=.
set BUILDDIR=_build

if "%SPHINXBUILD%"=="" (
  set SPHINXBUILD=python -m sphinx
)

if "%1"=="" goto help

%SPHINXBUILD% -W -b %1 "%SOURCEDIR%" "%BUILDDIR%\%1"
goto end

:help
%SPHINXBUILD% -M help "%SOURCEDIR%" "%BUILDDIR%"

:end
