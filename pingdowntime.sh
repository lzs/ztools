#!/bin/sh

DEADLIMIT=10
PINGINTERVAL=30

DEADTIME=0

do_help() {
        command=`basename $0`
        cat <<EOM
Usage $basename [options] <ip-address>

EOM
}

while getopts "h?s:" opt; do
        case "$opt" in
        h|\?)
                do_help
                exit 0
                ;;
        esac
done

shift $((OPTIND-1))

if [ "$#" -eq 0 ]; then
    do_help
    exit 0
fi

IP=$1

START=`date +%s`

DEADCOUNT=0

while true; do
    if (ping -c3 -q -W1 $IP > /dev/null) ; then
        echo PING OK
        DEADCOUNT=0
        sleep $PINGINTERVAL
    else
        if [ $DEADCOUNT -eq 0 ]; then
            DEADTIME=`date +%s`
        fi

        DEADCOUNT=$((DEADCOUNT + 1))
        echo DEBUG: Ping failed, DEADCOUNT=$DEADCOUNT

        if [ $DEADCOUNT -gt $DEADLIMIT ]; then
            break
        fi
        sleep $((DEADCOUNT * 2))
    fi
done


AGE=$((DEADTIME - START))
echo Dead at `date -d @$DEADTIME`
echo Ran for $AGE seconds
